"""Phase 5 step 2 review workflow tests: detail payload, PATCH + revalidation,
approval (status gate + audit + role guard), reprocess (queued + broker
failure), queued-state processing, and the vendors helper endpoints."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.workers.tasks as tasks_module
from app.api.v1.invoices import DOWNLOAD_URL_TTL_SECONDS
from app.main import app
from app.models import (
    AuditLog,
    DocumentFile,
    ExtractionEngine,
    ExtractionRun,
    ExtractionRunStatus,
    Invoice,
    InvoiceItem,
    InvoiceStatus,
    OrganizationMember,
    OrganizationRole,
    Vendor,
)
from app.modules.extraction.orchestrator import ExtractionOrchestrator
from app.workers.enqueue import get_enqueue_process_invoice
from tests.conftest import auth_headers
from tests.test_uploads import seed_invoice


class FakeStorage:
    """Records sign requests; returns deterministic URLs. No network."""

    def __init__(self) -> None:
        self.download_calls: list[dict[str, object]] = []

    async def create_signed_upload_url(self, *, bucket: str, path: str):
        raise AssertionError("uploads should not sign URLs in review tests")

    async def create_signed_download_url(
        self, *, bucket: str, path: str, expires_in_seconds: int
    ) -> str:
        self.download_calls.append(
            {
                "bucket": bucket,
                "path": path,
                "expires_in_seconds": expires_in_seconds,
            }
        )
        return f"https://storage.test/sign/{bucket}/{path}?token=t"


@pytest.fixture
def fake_storage(client: AsyncClient) -> FakeStorage:
    fake = FakeStorage()
    from app.modules.uploads.service import get_storage_client

    app.dependency_overrides[get_storage_client] = lambda: fake
    return fake


@pytest.fixture
def enqueue_calls(client: AsyncClient) -> list[str]:
    calls: list[str] = []

    async def fake_enqueue(invoice_id: str) -> bool:
        calls.append(invoice_id)
        return True

    app.dependency_overrides[get_enqueue_process_invoice] = lambda: fake_enqueue
    return calls


async def seed_reviewable_invoice(
    session_factory: async_sessionmaker,
    seeded,
    *,
    filename: str = "review-me.pdf",
    status: InvoiceStatus = InvoiceStatus.NEEDS_REVIEW,
    with_items: bool = True,
) -> Invoice:
    document = DocumentFile(
        id=uuid.uuid4(),
        organization_id=seeded["workspace_a_id"],
        storage_path=f"{seeded['workspace_a_id']}/2026/11/{uuid.uuid4()}_{filename}",
        original_filename=filename,
        mime_type="application/pdf",
        file_size_bytes=4096,
        content_hash=uuid.uuid4().hex,
        uploaded_by_id=seeded["user_a_id"],
    )
    invoice = Invoice(
        id=uuid.uuid4(),
        organization_id=seeded["workspace_a_id"],
        document_id=document.id,
        status=status,
        invoice_number="INV-1042",
        invoice_date=date(2026, 10, 28),
        due_date=date(2026, 11, 27),
        currency_code="EUR",
        subtotal_amount=Decimal("30.0000"),
        tax_amount=Decimal("6.0000"),
        total_amount=Decimal("36.0000"),
    )
    rows: list[DocumentFile | Invoice | InvoiceItem | ExtractionRun] = [document, invoice]
    if with_items:
        rows.append(
            InvoiceItem(
                organization_id=seeded["workspace_a_id"],
                invoice_id=invoice.id,
                line_number=1,
                description="Consulting",
                quantity=Decimal("3"),
                unit_price=Decimal("10"),
                line_total=Decimal("30"),
                page_number=1,
                bbox={"unit": "normalized", "page_number": 1, "polygon": [[0.1, 0.1], [0.5, 0.1]]},
                source_engine=ExtractionEngine.AZURE_DI,
            )
        )
    rows.append(
        ExtractionRun(
            organization_id=seeded["workspace_a_id"],
            invoice_id=invoice.id,
            status=ExtractionRunStatus.SUCCEEDED,
            attempt=1,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            normalized_output={
                "bounding_boxes": {
                    "InvoiceId": {
                        "unit": "normalized",
                        "page_number": 1,
                        "polygon": [[0.2, 0.3], [0.4, 0.3]],
                    }
                }
            },
        )
    )
    async with session_factory() as session:
        session.add_all(rows)
        await session.commit()
    return invoice


def ws_headers(seeded, user: str = "user_a_id") -> dict[str, str]:
    return {
        **auth_headers(seeded[user], "user@example.com"),
        "X-Workspace-ID": str(seeded["workspace_a_id"]),
    }


# ── GET detail ───────────────────────────────────────────────────────────


async def test_get_invoice_detail_full_payload(
    client: AsyncClient, seeded, session_factory, fake_storage: FakeStorage
) -> None:
    invoice = await seed_reviewable_invoice(session_factory, seeded)

    response = await client.get(
        f"/api/v1/invoices/{invoice.id}", headers=ws_headers(seeded)
    )
    assert response.status_code == 200
    body = response.json()

    assert body["invoice"]["id"] == str(invoice.id)
    assert body["invoice"]["status"] == "needs_review"
    assert body["invoice"]["total_amount"] == "36.0000"
    assert body["invoice"]["validation_errors"] is None
    assert body["document"]["original_filename"] == "review-me.pdf"
    assert len(body["line_items"]) == 1
    item = body["line_items"][0]
    assert item["bbox"]["polygon"] == [[0.1, 0.1], [0.5, 0.1]]
    assert item["source_engine"] == "azure_di"
    assert body["field_boxes"]["InvoiceId"]["polygon"] == [[0.2, 0.3], [0.4, 0.3]]

    # Signed URL contract: 15-minute TTL passed to storage, workspace-scoped path
    assert body["file_url"].startswith("https://storage.test/sign/")
    assert fake_storage.download_calls[0]["expires_in_seconds"] == DOWNLOAD_URL_TTL_SECONDS
    assert fake_storage.download_calls[0]["path"].startswith(f"{seeded['workspace_a_id']}/")


async def test_get_detail_isolated_from_other_tenant(
    client: AsyncClient, seeded, session_factory, fake_storage
) -> None:
    invoice = await seed_reviewable_invoice(session_factory, seeded)
    response = await client.get(
        f"/api/v1/invoices/{invoice.id}",
        headers={
            **auth_headers(seeded["user_b_id"], "b@example.com"),
            "X-Workspace-ID": str(seeded["workspace_b_id"]),
        },
    )
    assert response.status_code == 404


# ── PATCH + validation re-run ────────────────────────────────────────────


async def test_patch_updates_fields_and_reruns_validation(
    client: AsyncClient, seeded, session_factory, fake_storage
) -> None:
    invoice = await seed_reviewable_invoice(session_factory, seeded)
    # Break the total equation on purpose: 30 + 6 ≠ 40
    response = await client.patch(
        f"/api/v1/invoices/{invoice.id}",
        json={"total_amount": "40.00"},
        headers=ws_headers(seeded),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["invoice"]["total_amount"] == "40.0000"
    codes = {issue["code"] for issue in body["invoice"]["validation_errors"]}
    assert "MATH_TOTAL_MISMATCH" in codes

    async with session_factory() as session:
        updated = await session.get(Invoice, invoice.id)
        assert updated is not None
        assert updated.total_amount == Decimal("40.0000")
        assert updated.validation_errors is not None
        assert any(
            issue["code"] == "MATH_TOTAL_MISMATCH"
            for issue in updated.validation_errors
        )
        # Audit trail recorded the correction
        audits = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "invoice.corrected")
                )
            )
            .scalars()
            .all()
        )
        assert any(a.entity_id == invoice.id for a in audits)


async def test_patch_replaces_line_items_marked_corrected(
    client: AsyncClient, seeded, session_factory, fake_storage
) -> None:
    invoice = await seed_reviewable_invoice(session_factory, seeded)
    response = await client.patch(
        f"/api/v1/invoices/{invoice.id}",
        json={
            "subtotal": "80.00",
            "tax_amount": "16.00",
            "total_amount": "96.00",
            "line_items": [
                {"description": "Consulting", "quantity": "3", "unit_price": "10.00", "line_total": "30.00"},
                {"description": "Support", "quantity": "1", "unit_price": "50.00", "line_total": "50.00"},
            ],
        },
        headers=ws_headers(seeded),
    )
    assert response.status_code == 200
    assert len(response.json()["line_items"]) == 2
    assert response.json()["invoice"]["validation_errors"] == []

    async with session_factory() as session:
        items = (
            (
                await session.execute(
                    select(InvoiceItem)
                    .where(InvoiceItem.invoice_id == invoice.id)
                    .order_by(InvoiceItem.line_number)
                )
            )
            .scalars()
            .all()
        )
        assert len(items) == 2
        assert all(item.is_corrected for item in items)
        assert all(item.source_engine.value == "manual" for item in items)


async def test_patch_rejects_edited_status_and_foreign_vendor(
    client: AsyncClient, seeded, session_factory, fake_storage
) -> None:
    approved = await seed_reviewable_invoice(
        session_factory, seeded, filename="approved.pdf", status=InvoiceStatus.APPROVED
    )
    response = await client.patch(
        f"/api/v1/invoices/{approved.id}",
        json={"invoice_number": "NOPE"},
        headers=ws_headers(seeded),
    )
    assert response.status_code == 409

    # Foreign vendor id (belongs to nothing / another workspace) → 422
    reviewable = await seed_reviewable_invoice(session_factory, seeded, filename="v.pdf")
    response = await client.patch(
        f"/api/v1/invoices/{reviewable.id}",
        json={"vendor_id": str(uuid.uuid4())},
        headers=ws_headers(seeded),
    )
    assert response.status_code == 422


async def test_viewer_role_cannot_mutate(client: AsyncClient, seeded, session_factory, fake_storage) -> None:
    invoice = await seed_reviewable_invoice(session_factory, seeded)
    async with session_factory() as session:
        session.add(
            OrganizationMember(
                organization_id=seeded["workspace_a_id"],
                user_id=seeded["user_b_id"],
                role=OrganizationRole.VIEWER,
            )
        )
        await session.commit()

    viewer_headers = {
        **auth_headers(seeded["user_b_id"], "b@example.com"),
        "X-Workspace-ID": str(seeded["workspace_a_id"]),
    }
    patch = await client.patch(
        f"/api/v1/invoices/{invoice.id}", json={"invoice_number": "X"}, headers=viewer_headers
    )
    approve = await client.post(
        f"/api/v1/invoices/{invoice.id}/approve", headers=viewer_headers
    )
    assert patch.status_code == 403
    assert approve.status_code == 403
    # But viewers can still READ the detail
    detail = await client.get(f"/api/v1/invoices/{invoice.id}", headers=viewer_headers)
    assert detail.status_code == 200


# ── Approve ──────────────────────────────────────────────────────────────


async def test_approve_records_actor_and_audit(
    client: AsyncClient, seeded, session_factory, fake_storage
) -> None:
    invoice = await seed_reviewable_invoice(session_factory, seeded)
    response = await client.post(
        f"/api/v1/invoices/{invoice.id}/approve", headers=ws_headers(seeded)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["invoice"]["status"] == "approved"
    assert body["invoice"]["approved_by_id"] == str(seeded["user_a_id"])
    assert body["invoice"]["approved_at"] is not None

    async with session_factory() as session:
        audits = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "invoice.approved")
                )
            )
            .scalars()
            .all()
        )
        assert any(
            a.actor_id == seeded["user_a_id"] and a.entity_id == invoice.id for a in audits
        )


async def test_approve_is_rejected_from_wrong_status(
    client: AsyncClient, seeded, session_factory, fake_storage
) -> None:
    uploaded = await seed_reviewable_invoice(
        session_factory, seeded, filename="u.pdf", status=InvoiceStatus.UPLOADED
    )
    response = await client.post(
        f"/api/v1/invoices/{uploaded.id}/approve", headers=ws_headers(seeded)
    )
    assert response.status_code == 409

    # Second approve after already approved → 409
    reviewable = await seed_reviewable_invoice(session_factory, seeded, filename="r.pdf")
    first = await client.post(
        f"/api/v1/invoices/{reviewable.id}/approve", headers=ws_headers(seeded)
    )
    assert first.status_code == 200
    second = await client.post(
        f"/api/v1/invoices/{reviewable.id}/approve", headers=ws_headers(seeded)
    )
    assert second.status_code == 409


# ── Reprocess ────────────────────────────────────────────────────────────


async def test_reprocess_queues_and_worker_processes(
    client: AsyncClient,
    seeded,
    session_factory,
    fake_storage: FakeStorage,
    enqueue_calls: list[str],
    monkeypatch,
) -> None:
    invoice = await seed_reviewable_invoice(session_factory, seeded)
    response = await client.post(
        f"/api/v1/invoices/{invoice.id}/reprocess", headers=ws_headers(seeded)
    )
    assert response.status_code == 200
    assert response.json()["invoice"]["status"] == "queued"
    assert enqueue_calls == [str(invoice.id)]

    # The worker's guard now accepts `queued` — running the task processes it.
    async def instant_stage(factory, invoice_uuid, run_id):
        return None

    monkeypatch.setattr(tasks_module, "_run_extraction", instant_stage)
    result = await tasks_module.process_invoice_task(
        {"session_factory": session_factory}, str(invoice.id)
    )
    assert result == "needs_review"
    async with session_factory() as session:
        updated = await session.get(Invoice, invoice.id)
        assert updated is not None
        assert updated.status == InvoiceStatus.NEEDS_REVIEW


async def test_reprocess_with_redis_down_returns_503_and_keeps_status(
    client: AsyncClient, seeded, session_factory, fake_storage
) -> None:
    async def failing_enqueue(invoice_id: str) -> bool:
        return False

    app.dependency_overrides[get_enqueue_process_invoice] = lambda: failing_enqueue

    invoice = await seed_reviewable_invoice(session_factory, seeded)
    response = await client.post(
        f"/api/v1/invoices/{invoice.id}/reprocess", headers=ws_headers(seeded)
    )
    assert response.status_code == 503
    async with session_factory() as session:
        unchanged = await session.get(Invoice, invoice.id)
        assert unchanged is not None
        assert unchanged.status == InvoiceStatus.NEEDS_REVIEW  # not stuck queued


# ── Vendors helper endpoints ─────────────────────────────────────────────


async def test_vendor_create_and_list(client: AsyncClient, seeded) -> None:
    headers = ws_headers(seeded)
    created = await client.post(
        "/api/v1/vendors",
        json={"name": "Acme GmbH", "tax_id": "DE811907980", "default_currency": "EUR"},
        headers=headers,
    )
    assert created.status_code == 201
    assert created.json()["name"] == "Acme GmbH"

    duplicate = await client.post(
        "/api/v1/vendors", json={"name": "Acme GmbH"}, headers=headers
    )
    assert duplicate.status_code == 409

    listed = await client.get("/api/v1/vendors", headers=headers)
    assert listed.status_code == 200
    names = [vendor["name"] for vendor in listed.json()]
    assert names == ["Acme GmbH"]

    searchable = await client.get(
        "/api/v1/vendors", headers=headers, params={"q": "acme"}
    )
    assert len(searchable.json()) == 1
