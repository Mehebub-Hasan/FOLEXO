"""Phase 4 worker tests: enqueue-on-ingestion, status endpoint, and the
invoice state machine (UPLOADED → PROCESSING → NEEDS_REVIEW | FAILED).

Runs on the same SQLite harness as the rest of the suite; Redis/ARQ is
simulated via the `get_enqueue_process_invoice` dependency override, and the
task is invoked directly with a test session factory (no broker needed)."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.workers.tasks as tasks_module
from app.main import app
from app.models import ExtractionRun, Invoice, InvoiceStatus
from app.workers.enqueue import get_enqueue_process_invoice
from app.workers.tasks import process_invoice_task
from tests.conftest import auth_headers
from tests.test_uploads import (
    LIMIT_BYTES,
    FRESH_HASH,
    invoice_payload,
    seed_invoice,
    workspace_headers,
)


@pytest.fixture
def enqueue_calls(client: AsyncClient) -> list[str]:
    """Replace the broker with a recorder; returns the list of enqueued ids."""
    calls: list[str] = []

    async def fake_enqueue(invoice_id: str) -> bool:
        calls.append(invoice_id)
        return True

    app.dependency_overrides[get_enqueue_process_invoice] = lambda: fake_enqueue
    return calls


@pytest.fixture
def redis_down(client: AsyncClient) -> None:
    """Simulate Redis being unreachable."""

    async def failing_enqueue(invoice_id: str) -> bool:
        return False

    app.dependency_overrides[get_enqueue_process_invoice] = lambda: failing_enqueue


def workspace_a_headers(seeded: dict[str, uuid.UUID]) -> dict[str, str]:
    return workspace_headers(seeded, "user_a_id")


# ── Enqueue on ingestion ─────────────────────────────────────────────────


async def test_create_invoice_enqueues_processing(
    client: AsyncClient, seeded: dict[str, uuid.UUID], enqueue_calls: list[str]
) -> None:
    response = await client.post(
        "/api/v1/invoices",
        json=invoice_payload(f"{seeded['workspace_a_id']}/2026/11/{uuid.uuid4()}_x.pdf"),
        headers=workspace_a_headers(seeded),
    )
    assert response.status_code == 201
    assert enqueue_calls == [response.json()["invoice"]["id"]]


async def test_create_invoice_survives_redis_unavailability(
    client: AsyncClient, seeded: dict[str, uuid.UUID], redis_down
) -> None:
    """Redis down must not fail the upload; the invoice stays UPLOADED."""
    response = await client.post(
        "/api/v1/invoices",
        json=invoice_payload(f"{seeded['workspace_a_id']}/2026/11/{uuid.uuid4()}_x.pdf"),
        headers=workspace_a_headers(seeded),
    )
    assert response.status_code == 201
    assert response.json()["invoice"]["status"] == "uploaded"


# ── Status endpoint ──────────────────────────────────────────────────────


async def test_status_endpoint_returns_correct_schema(
    client: AsyncClient, seeded: dict[str, uuid.UUID], session_factory
) -> None:
    invoice = await seed_invoice(
        session_factory,
        organization_id=seeded["workspace_a_id"],
        uploader_id=seeded["user_a_id"],
        filename="status-uploaded.pdf",
    )
    response = await client.get(
        f"/api/v1/invoices/{invoice.id}/status", headers=workspace_a_headers(seeded)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["invoice_id"] == str(invoice.id)
    assert body["status"] == "uploaded"
    assert body["progress_percent"] == 0
    assert body["error_message"] is None


async def test_status_endpoint_processing_progress(
    client: AsyncClient, seeded: dict[str, uuid.UUID], session_factory
) -> None:
    invoice = await seed_invoice(
        session_factory,
        organization_id=seeded["workspace_a_id"],
        uploader_id=seeded["user_a_id"],
        filename="status-processing.pdf",
        status=InvoiceStatus.PROCESSING,
    )
    response = await client.get(
        f"/api/v1/invoices/{invoice.id}/status", headers=workspace_a_headers(seeded)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "processing"
    assert body["progress_percent"] == 50


async def test_status_endpoint_isolated_from_other_tenant(
    client: AsyncClient, seeded: dict[str, uuid.UUID], session_factory
) -> None:
    """User B querying workspace A's invoice id gets 404 — no existence probe."""
    invoice = await seed_invoice(
        session_factory,
        organization_id=seeded["workspace_a_id"],
        uploader_id=seeded["user_a_id"],
        filename="a-private.pdf",
    )
    response = await client.get(
        f"/api/v1/invoices/{invoice.id}/status",
        headers={
            **auth_headers(seeded["user_b_id"], "b@example.com"),
            "X-Workspace-ID": str(seeded["workspace_b_id"]),
        },
    )
    assert response.status_code == 404


async def test_status_endpoint_surfaces_error_after_failure(
    client: AsyncClient, seeded: dict[str, uuid.UUID], session_factory, monkeypatch
) -> None:
    invoice = await seed_invoice(
        session_factory,
        organization_id=seeded["workspace_a_id"],
        uploader_id=seeded["user_a_id"],
        filename="status-failed.pdf",
    )

    async def exploding_stage() -> None:
        raise RuntimeError("stage exploded")

    monkeypatch.setattr(tasks_module, "run_mock_extraction", exploding_stage)
    result = await process_invoice_task(
        {"session_factory": session_factory}, str(invoice.id)
    )
    assert result == "failed"

    response = await client.get(
        f"/api/v1/invoices/{invoice.id}/status", headers=workspace_a_headers(seeded)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["progress_percent"] == 100
    assert body["error_message"] == "stage exploded"


# ── Task state machine ───────────────────────────────────────────────────


async def test_task_state_machine_uploaded_to_needs_review(
    seeded: dict[str, uuid.UUID], session_factory, monkeypatch
) -> None:
    invoice = await seed_invoice(
        session_factory,
        organization_id=seeded["workspace_a_id"],
        uploader_id=seeded["user_a_id"],
        filename="state-machine.pdf",
    )
    monkeypatch.setattr(tasks_module, "MOCK_STAGE_DELAY_SECONDS", 0.01)

    result = await process_invoice_task(
        {"session_factory": session_factory}, str(invoice.id)
    )

    assert result == "needs_review"
    async with session_factory() as session:
        updated = await session.get(Invoice, invoice.id)
        assert updated is not None
        assert updated.status == InvoiceStatus.NEEDS_REVIEW
        assert updated.extraction_started_at is not None
        assert updated.extraction_finished_at is not None

        runs = (
            await session.execute(
                select(ExtractionRun).where(ExtractionRun.invoice_id == invoice.id)
            )
        ).scalars().all()
        assert len(runs) == 1
        run = runs[0]
        assert run.status.value == "succeeded"
        assert run.started_at is not None
        assert run.finished_at is not None
        assert run.duration_ms is not None and run.duration_ms >= 0
        assert run.error_message is None


async def test_task_does_not_resurrect_non_uploaded_invoice(
    seeded: dict[str, uuid.UUID], session_factory
) -> None:
    """A stale arq redelivery must not touch an invoice already past UPLOADED."""
    invoice = await seed_invoice(
        session_factory,
        organization_id=seeded["workspace_a_id"],
        uploader_id=seeded["user_a_id"],
        filename="already-reviewed.pdf",
        status=InvoiceStatus.NEEDS_REVIEW,
    )
    result = await process_invoice_task(
        {"session_factory": session_factory}, str(invoice.id)
    )
    assert result == "skipped"
    async with session_factory() as session:
        unchanged = await session.get(Invoice, invoice.id)
        assert unchanged is not None
        assert unchanged.status == InvoiceStatus.NEEDS_REVIEW


async def test_task_ignores_unknown_invoice(session_factory) -> None:
    result = await process_invoice_task(
        {"session_factory": session_factory}, str(uuid.uuid4())
    )
    assert result == "not_found"
