"""Phase 6 step 1 vendor tests: directory metrics, supplier history,
tenant isolation, and find_or_create_vendor deduplication."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import (
    DocumentFile,
    Invoice,
    InvoiceStatus,
    Vendor,
)
from app.modules.vendors.matcher import (
    find_or_create_vendor,
    normalize_vendor_name,
)
from tests.conftest import auth_headers
from tests.test_uploads import seed_invoice


async def seed_vendor_row(
    session_factory, seeded, *, name: str, organization_id: uuid.UUID | None = None
) -> Vendor:
    vendor = Vendor(
        id=uuid.uuid4(),
        organization_id=organization_id or seeded["workspace_a_id"],
        name=name,
        name_norm=normalize_vendor_name(name),
    )
    async with session_factory() as session:
        session.add(vendor)
        await session.commit()
    return vendor


async def seed_vendor_invoice(
    session_factory,
    seeded,
    vendor: Vendor,
    *,
    status: InvoiceStatus,
    total: str,
    invoice_date: date,
) -> Invoice:
    document = DocumentFile(
        id=uuid.uuid4(),
        organization_id=seeded["workspace_a_id"],
        storage_path=f"{seeded['workspace_a_id']}/2026/11/{uuid.uuid4()}.pdf",
        original_filename=f"{uuid.uuid4().hex}.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        content_hash=uuid.uuid4().hex,
        uploaded_by_id=seeded["user_a_id"],
    )
    invoice = Invoice(
        id=uuid.uuid4(),
        organization_id=seeded["workspace_a_id"],
        document_id=document.id,
        vendor_id=vendor.id,
        invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
        total_amount=Decimal(total),
        invoice_date=invoice_date,
        status=status,
    )
    async with session_factory() as session:
        session.add_all([document, invoice])
        await session.commit()
    return invoice


def ws_headers(seeded, user: str = "user_a_id") -> dict[str, str]:
    return {
        **auth_headers(seeded[user], "user@example.com"),
        "X-Workspace-ID": str(seeded["workspace_a_id"]),
    }


# ── Directory metrics ────────────────────────────────────────────────────


async def test_spend_aggregation_counts_all_but_sums_approved_only(
    client: AsyncClient, seeded, session_factory
) -> None:
    vendor = await seed_vendor_row(session_factory, seeded, name="Acme GmbH")
    # approved: 100 + 200 = 300 (only these count toward spend)
    await seed_vendor_invoice(
        session_factory, seeded, vendor, status=InvoiceStatus.APPROVED,
        total="100.00", invoice_date=date(2026, 10, 1),
    )
    await seed_vendor_invoice(
        session_factory, seeded, vendor, status=InvoiceStatus.APPROVED,
        total="200.00", invoice_date=date(2026, 10, 15),
    )
    # needs_review + failed: count toward invoice_count, NOT spend
    await seed_vendor_invoice(
        session_factory, seeded, vendor, status=InvoiceStatus.NEEDS_REVIEW,
        total="999.00", invoice_date=date(2026, 11, 1),
    )
    await seed_vendor_invoice(
        session_factory, seeded, vendor, status=InvoiceStatus.FAILED,
        total="50.00", invoice_date=date(2026, 11, 2),
    )

    response = await client.get("/api/v1/vendors", headers=ws_headers(seeded))
    assert response.status_code == 200
    entries = response.json()
    assert len(entries) == 1

    entry = entries[0]
    assert entry["name"] == "Acme GmbH"
    assert entry["normalized_name"] == "acme"
    assert entry["invoice_count"] == 4  # every status counts
    assert Decimal(entry["total_spend"]) == Decimal("300.00")  # approved only
    assert entry["last_invoice_date"] == "2026-11-02"  # max invoice_date, all statuses


async def test_directory_search_matches_name_and_tax_id(
    client: AsyncClient, seeded, session_factory
) -> None:
    await seed_vendor_row(session_factory, seeded, name="Beta Logistics")
    acme = await seed_vendor_row(session_factory, seeded, name="Acme GmbH")
    async with session_factory() as session:
        acme_row = await session.get(Vendor, acme.id)
        acme_row.tax_id = "DE811907980"
        await session.commit()

    by_name = await client.get(
        "/api/v1/vendors", headers=ws_headers(seeded), params={"search": "beta"}
    )
    assert [v["name"] for v in by_name.json()] == ["Beta Logistics"]

    by_tax = await client.get(
        "/api/v1/vendors", headers=ws_headers(seeded), params={"search": "DE811907980"}
    )
    assert [v["name"] for v in by_tax.json()] == ["Acme GmbH"]


# ── Workspace isolation ──────────────────────────────────────────────────


async def test_workspace_isolation_on_list_and_detail(
    client: AsyncClient, seeded, session_factory
) -> None:
    vendor_b = await seed_vendor_row(
        session_factory, seeded, name="B Corp", organization_id=seeded["workspace_b_id"]
    )

    # Workspace A's directory must not contain workspace B's vendor
    listing = await client.get("/api/v1/vendors", headers=ws_headers(seeded))
    assert listing.status_code == 200
    assert all(entry["id"] != str(vendor_b.id) for entry in listing.json())

    # Direct detail access from workspace A → 404
    detail = await client.get(
        f"/api/v1/vendors/{vendor_b.id}", headers=ws_headers(seeded)
    )
    assert detail.status_code == 404


# ── Supplier history ─────────────────────────────────────────────────────


async def test_vendor_detail_returns_paginated_history(
    client: AsyncClient, seeded, session_factory
) -> None:
    vendor = await seed_vendor_row(session_factory, seeded, name="Acme GmbH")
    for index in range(3):
        await seed_vendor_invoice(
            session_factory, seeded, vendor, status=InvoiceStatus.APPROVED,
            total=f"{100 + index}.00", invoice_date=date(2026, 10, 1 + index),
        )

    page = await client.get(
        f"/api/v1/vendors/{vendor.id}",
        headers=ws_headers(seeded),
        params={"limit": 2, "offset": 0},
    )
    assert page.status_code == 200
    body = page.json()
    assert body["invoice_count"] == 3
    assert body["pagination"] == {"total": 3, "limit": 2, "offset": 0}
    assert len(body["invoices"]) == 2
    assert body["invoices"][0]["invoice_date"] == "2026-10-03"  # newest first

    page2 = await client.get(
        f"/api/v1/vendors/{vendor.id}",
        headers=ws_headers(seeded),
        params={"limit": 2, "offset": 2},
    )
    assert len(page2.json()["invoices"]) == 1


# ── PATCH ────────────────────────────────────────────────────────────────


async def test_patch_updates_contact_fields(
    client: AsyncClient, seeded, session_factory
) -> None:
    vendor = await seed_vendor_row(session_factory, seeded, name="Acme GmbH")
    response = await client.patch(
        f"/api/v1/vendors/{vendor.id}",
        json={
            "email": "accounts@acme.example",
            "address": "Hauptstr. 1, 10115 Berlin",
            "tax_identifier": "DE811907980",
            "notes": "Net 30, prefers PDF.",
        },
        headers=ws_headers(seeded),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "accounts@acme.example"
    assert body["address"] == "Hauptstr. 1, 10115 Berlin"
    assert body["tax_identifier"] == "DE811907980"
    assert body["notes"] == "Net 30, prefers PDF."


# ── find_or_create_vendor ────────────────────────────────────────────────


async def test_matcher_dedup_by_tax_id_then_name_then_create(
    seeded, session_factory
) -> None:
    existing = await seed_vendor_row(session_factory, seeded, name="Acme GmbH")
    async with session_factory() as session:
        existing_row = await session.get(Vendor, existing.id)
        existing_row.tax_id = "DE811907980"
        await session.commit()

    # 1. Tax-id match wins even when the name differs
    async with session_factory() as db:
        by_tax = await find_or_create_vendor(db, seeded["workspace_a_id"], "Acme Industries", "DE811907980")
    assert by_tax is not None and by_tax.id == existing.id

    # 2. Normalized-name match (suffix-insensitive: GmbH vs Inc vs Ltd)
    async with session_factory() as db:
        by_name = await find_or_create_vendor(db, seeded["workspace_a_id"], "Acme Inc", None)
    assert by_name is not None and by_name.id == existing.id

    # 3. No match → created with normalized name
    async with session_factory() as db:
        created = await find_or_create_vendor(
            db,
            seeded["workspace_a_id"],
            "Beta Logistics Ltd",
            None,
            created_by_id=seeded["user_a_id"],
            default_currency="EUR",
        )
        # The matcher only flushes — the caller owns the transaction.
        await db.commit()
    assert created is not None and created.id != existing.id
    assert created.name == "Beta Logistics Ltd"
    assert created.name_norm == "beta logistics"
    assert created.default_currency == "EUR"

    async with session_factory() as db:
        vendors = (await db.execute(select(Vendor))).scalars().all()
    assert len(vendors) == 2  # existing + created; "Acme Inc" did not duplicate


async def test_matcher_normalization_strips_suffixes() -> None:
    assert normalize_vendor_name("Acme GmbH") == "acme"
    assert normalize_vendor_name("  Acme   Ltd. ") == "acme"
    assert normalize_vendor_name("Beta Logistics Inc.") == "beta logistics"
    assert normalize_vendor_name("Co") == "co"  # single token kept intact
    assert normalize_vendor_name("") == ""


async def test_matcher_returns_none_without_usable_identity(
    seeded, session_factory
) -> None:
    async with session_factory() as db:
        result = await find_or_create_vendor(db, seeded["workspace_a_id"], "   ", None)
    assert result is None
    async with session_factory() as db:
        vendors = (await db.execute(select(Vendor))).scalars().all()
    assert vendors == []
