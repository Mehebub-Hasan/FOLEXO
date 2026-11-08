"""Phase 3 ingestion tests: presign constraints, duplicate detection, and
tenant-scoped invoice + document creation.

Runs against the same in-process SQLite harness as test_auth.py, with the
Supabase storage client replaced by a fake (no network)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app
from app.models import AuditLog, DocumentFile, Invoice, InvoiceStatus
from app.modules.uploads.service import SignedUpload, get_storage_client
from tests.conftest import auth_headers

LIMIT_BYTES = 15 * 1024 * 1024
FRESH_HASH = "b" * 64


class FakeStorageClient:
    """Records sign requests; returns deterministic URLs. No network."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def create_signed_upload_url(self, *, bucket: str, path: str) -> SignedUpload:
        self.calls.append({"bucket": bucket, "path": path})
        return SignedUpload(upload_url=f"https://storage.test/{bucket}/{path}", token="test-token")


@pytest.fixture
def fake_storage(client: AsyncClient) -> FakeStorageClient:
    fake = FakeStorageClient()
    app.dependency_overrides[get_storage_client] = lambda: fake
    return fake


def presign_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "filename": "acme-inv-1042.pdf",
        "file_size_bytes": LIMIT_BYTES,  # exactly at the limit → allowed
        "mime_type": "application/pdf",
        "sha256_hash": FRESH_HASH,
    }
    payload.update(overrides)
    return payload


def workspace_headers(seeded: dict[str, uuid.UUID], user: str = "user_a_id") -> dict[str, str]:
    return {
        **auth_headers(seeded[user], "user@example.com"),
        "X-Workspace-ID": str(seeded["workspace_a_id"]),
    }


async def seed_document_with_invoice(
    session_factory: async_sessionmaker,
    *,
    organization_id: uuid.UUID,
    uploader_id: uuid.UUID,
    content_hash: str,
) -> Invoice:
    document = DocumentFile(
        id=uuid.uuid4(),
        organization_id=organization_id,
        storage_path=f"{organization_id}/2026/11/{uuid.uuid4()}_seed.pdf",
        original_filename="seed.pdf",
        mime_type="application/pdf",
        file_size_bytes=2048,
        content_hash=content_hash,
        uploaded_by_id=uploader_id,
    )
    invoice = Invoice(
        id=uuid.uuid4(),
        organization_id=organization_id,
        document_id=document.id,
        status=InvoiceStatus.UPLOADED,
    )
    async with session_factory() as session:
        session.add_all([document, invoice])
        await session.commit()
    return invoice


# ── Presign guardrails ───────────────────────────────────────────────────


async def test_unauthenticated_presign_returns_401(client: AsyncClient, fake_storage) -> None:
    response = await client.post("/api/v1/uploads/presign", json=presign_payload())
    assert response.status_code == 401


async def test_presign_rejects_oversized_file_with_413(
    client: AsyncClient, seeded: dict[str, uuid.UUID], fake_storage
) -> None:
    response = await client.post(
        "/api/v1/uploads/presign",
        json=presign_payload(file_size_bytes=LIMIT_BYTES + 1),
        headers=workspace_headers(seeded),
    )
    assert response.status_code == 413
    assert "15 MB" in response.json()["detail"]
    assert fake_storage.calls == []  # rejected before any storage call


async def test_presign_allows_file_at_limit(
    client: AsyncClient, seeded: dict[str, uuid.UUID], fake_storage
) -> None:
    response = await client.post(
        "/api/v1/uploads/presign",
        json=presign_payload(file_size_bytes=LIMIT_BYTES),
        headers=workspace_headers(seeded),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_duplicate"] is False
    assert body["existing_invoice_id"] is None
    # Path contract: {organization_id}/{year}/{month}/{uuid}_{clean_filename}
    workspace_prefix = f"{seeded['workspace_a_id']}/"
    assert body["storage_path"].startswith(workspace_prefix)
    assert body["storage_path"].endswith("_acme-inv-1042.pdf")
    assert fake_storage.calls[0]["bucket"] == "invoices"
    assert fake_storage.calls[0]["path"] == body["storage_path"]


@pytest.mark.parametrize("mime_type", ["text/html", "application/zip", "image/tiff"])
async def test_presign_rejects_invalid_mime_types_with_415(
    client: AsyncClient, seeded: dict[str, uuid.UUID], fake_storage, mime_type: str
) -> None:
    response = await client.post(
        "/api/v1/uploads/presign",
        json=presign_payload(mime_type=mime_type),
        headers=workspace_headers(seeded),
    )
    assert response.status_code == 415


async def test_presign_rejects_malformed_hash_with_422(
    client: AsyncClient, seeded: dict[str, uuid.UUID], fake_storage
) -> None:
    response = await client.post(
        "/api/v1/uploads/presign",
        json=presign_payload(sha256_hash="not-a-hash"),
        headers=workspace_headers(seeded),
    )
    assert response.status_code == 422


# ── Duplicate detection ──────────────────────────────────────────────────


async def test_presign_flags_duplicate_hash_with_existing_invoice(
    client: AsyncClient, seeded: dict[str, uuid.UUID], fake_storage, session_factory
) -> None:
    existing = await seed_document_with_invoice(
        session_factory,
        organization_id=seeded["workspace_a_id"],
        uploader_id=seeded["user_a_id"],
        content_hash=FRESH_HASH,
    )

    response = await client.post(
        "/api/v1/uploads/presign",
        json=presign_payload(sha256_hash=FRESH_HASH.upper()),  # case-insensitive lookup
        headers=workspace_headers(seeded),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_duplicate"] is True
    assert body["existing_invoice_id"] == str(existing.id)

    # The same hash in ANOTHER tenant must not be flagged for this tenant.
    await seed_document_with_invoice(
        session_factory,
        organization_id=seeded["workspace_b_id"],
        uploader_id=seeded["user_b_id"],
        content_hash="c" * 64,
    )
    fresh = await client.post(
        "/api/v1/uploads/presign",
        json=presign_payload(sha256_hash="c" * 64),
        headers=workspace_headers(seeded),
    )
    assert fresh.status_code == 200
    assert fresh.json()["is_duplicate"] is False


# ── POST /api/v1/invoices ────────────────────────────────────────────────


def invoice_payload(storage_path: str) -> dict[str, object]:
    return {
        "storage_path": storage_path,
        "file_hash": FRESH_HASH,
        "original_filename": "acme-inv-1042.pdf",
        "mime_type": "application/pdf",
        "file_size_bytes": 184_322,
    }


async def test_create_invoice_scopes_records_to_tenant(
    client: AsyncClient, seeded: dict[str, uuid.UUID], session_factory
) -> None:
    storage_path = f"{seeded['workspace_a_id']}/2026/11/{uuid.uuid4()}_acme-inv-1042.pdf"
    response = await client.post(
        "/api/v1/invoices",
        json=invoice_payload(storage_path),
        headers=workspace_headers(seeded),
    )
    assert response.status_code == 201
    body = response.json()

    assert body["invoice"]["organization_id"] == str(seeded["workspace_a_id"])
    assert body["invoice"]["status"] == "uploaded"
    assert body["document"]["content_hash"] == FRESH_HASH
    assert body["document"]["storage_path"] == storage_path

    # Verify persisted rows are tenant-scoped and linked.
    async with session_factory() as session:
        invoice = await session.get(Invoice, uuid.UUID(body["invoice"]["id"]))
        assert invoice is not None
        assert invoice.organization_id == seeded["workspace_a_id"]
        assert invoice.status == InvoiceStatus.UPLOADED

        document = await session.get(DocumentFile, invoice.document_id)
        assert document is not None
        assert document.organization_id == seeded["workspace_a_id"]
        assert document.uploaded_by_id == seeded["user_a_id"]

        audits = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "invoice.uploaded")
            )
        ).scalars().all()
        assert any(audit.entity_id == invoice.id for audit in audits)


async def test_create_invoice_rejects_storage_path_outside_workspace(
    client: AsyncClient, seeded: dict[str, uuid.UUID]
) -> None:
    """User B cannot ingest a storage path that belongs to workspace A."""
    foreign_path = f"{seeded['workspace_a_id']}/2026/11/{uuid.uuid4()}_stolen.pdf"
    response = await client.post(
        "/api/v1/invoices",
        json=invoice_payload(foreign_path),
        headers={
            **auth_headers(seeded["user_b_id"], "b@example.com"),
            "X-Workspace-ID": str(seeded["workspace_b_id"]),
        },
    )
    assert response.status_code == 403


async def test_create_invoice_revalidates_constraints(
    client: AsyncClient, seeded: dict[str, uuid.UUID]
) -> None:
    headers = workspace_headers(seeded)
    path = f"{seeded['workspace_a_id']}/2026/11/{uuid.uuid4()}_x.pdf"

    oversized = await client.post(
        "/api/v1/invoices",
        json=invoice_payload(path) | {"file_size_bytes": LIMIT_BYTES + 1},
        headers=headers,
    )
    assert oversized.status_code == 413

    bad_mime = await client.post(
        "/api/v1/invoices",
        json=invoice_payload(path) | {"mime_type": "application/zip"},
        headers=headers,
    )
    assert bad_mime.status_code == 415


# ── GET /api/v1/invoices — inbox listing ─────────────────────────────────


async def seed_invoice(
    session_factory: async_sessionmaker,
    *,
    organization_id: uuid.UUID,
    uploader_id: uuid.UUID,
    filename: str,
    status: InvoiceStatus = InvoiceStatus.UPLOADED,
    created_at: datetime | None = None,
) -> Invoice:
    document = DocumentFile(
        id=uuid.uuid4(),
        organization_id=organization_id,
        storage_path=f"{organization_id}/2026/11/{uuid.uuid4()}_{filename}",
        original_filename=filename,
        mime_type="application/pdf",
        file_size_bytes=1024,
        content_hash=uuid.uuid4().hex,
        uploaded_by_id=uploader_id,
    )
    invoice = Invoice(
        id=uuid.uuid4(),
        organization_id=organization_id,
        document_id=document.id,
        status=status,
        created_at=created_at,
    )
    async with session_factory() as session:
        session.add_all([document, invoice])
        await session.commit()
    return invoice


async def test_list_invoices_scoped_and_paginated(
    client: AsyncClient, seeded: dict[str, uuid.UUID], session_factory
) -> None:
    # Explicit, spaced timestamps: SQLite's CURRENT_TIMESTAMP has second
    # precision, so same-second rows would tie on created_at and make the
    # (created_at, id) keyset order backend-arbitrary.
    now = datetime.now(timezone.utc)
    timestamps = (now - timedelta(minutes=0), now - timedelta(minutes=1), now - timedelta(minutes=2))
    for name, created_at in zip(("a-one.pdf", "a-two.pdf", "a-three.pdf"), timestamps):
        await seed_invoice(
            session_factory,
            organization_id=seeded["workspace_a_id"],
            uploader_id=seeded["user_a_id"],
            filename=name,
            created_at=created_at,
        )
    await seed_invoice(
        session_factory,
        organization_id=seeded["workspace_b_id"],
        uploader_id=seeded["user_b_id"],
        filename="b-secret.pdf",
    )

    headers = workspace_headers(seeded)
    response = await client.get("/api/v1/invoices", headers=headers)
    assert response.status_code == 200
    body = response.json()
    names = [item["original_filename"] for item in body["data"]]
    assert names == ["a-one.pdf", "a-two.pdf", "a-three.pdf"]  # newest first
    assert "b-secret.pdf" not in names  # other tenant invisible
    assert body["pagination"]["has_more"] is False
    assert body["pagination"]["next_cursor"] is None

    # Keyset pagination: page 2 continues exactly where page 1 stopped.
    page1 = await client.get("/api/v1/invoices", headers=headers, params={"limit": 2})
    assert page1.status_code == 200
    page1_body = page1.json()
    assert [i["original_filename"] for i in page1_body["data"]] == names[:2]
    assert page1_body["pagination"]["has_more"] is True

    page2 = await client.get(
        "/api/v1/invoices",
        headers=headers,
        params={"limit": 2, "cursor": page1_body["pagination"]["next_cursor"]},
    )
    assert page2.status_code == 200
    assert [i["original_filename"] for i in page2.json()["data"]] == names[2:]
    assert page2.json()["pagination"]["has_more"] is False

    # Status filter
    await seed_invoice(
        session_factory,
        organization_id=seeded["workspace_a_id"],
        uploader_id=seeded["user_a_id"],
        filename="a-review.pdf",
        status=InvoiceStatus.NEEDS_REVIEW,
    )
    filtered = await client.get(
        "/api/v1/invoices", headers=headers, params={"status": "needs_review"}
    )
    assert filtered.status_code == 200
    assert [i["original_filename"] for i in filtered.json()["data"]] == ["a-review.pdf"]

    invalid = await client.get(
        "/api/v1/invoices", headers=headers, params={"status": "bogus"}
    )
    assert invalid.status_code == 422

    broken_cursor = await client.get(
        "/api/v1/invoices", headers=headers, params={"cursor": "@@not-a-cursor@@"}
    )
    assert broken_cursor.status_code == 400


async def test_ingestion_flow_presign_confirm_to_inbox(
    client: AsyncClient, seeded: dict[str, uuid.UUID], fake_storage
) -> None:
    """Full Phase-3 flow: presign → (bytes go straight to storage) → confirm →
    the invoice appears in the inbox with status UPLOADED."""
    headers = workspace_headers(seeded)

    presign = await client.post(
        "/api/v1/uploads/presign", json=presign_payload(), headers=headers
    )
    assert presign.status_code == 200
    presign_body = presign.json()
    assert presign_body["is_duplicate"] is False
    assert fake_storage.calls[0]["path"] == presign_body["storage_path"]
    # (The client PUTs the bytes to upload_url; the fake storage records it.)

    confirm = await client.post(
        "/api/v1/invoices",
        json=invoice_payload(presign_body["storage_path"]),
        headers=headers,
    )
    assert confirm.status_code == 201

    inbox = await client.get("/api/v1/invoices", headers=headers)
    assert inbox.status_code == 200
    items = inbox.json()["data"]
    assert len(items) == 1
    item = items[0]
    assert item["original_filename"] == "acme-inv-1042.pdf"
    assert item["status"] == "uploaded"
    assert item["vendor_name"] is None  # "Unassigned" placeholder on the client
    assert item["invoice_number"] is None
    assert item["total_amount"] is None
