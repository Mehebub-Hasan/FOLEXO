"""Phase 5 duplicate detection tests (multi-signal, tenant-scoped)."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import DocumentFile, Invoice, InvoiceStatus, Vendor
from app.modules.duplicates.detector import check_duplicates
from tests.conftest import seeded  # noqa: F401 — fixtures via conftest


async def seed_vendor(session_factory, seeded, *, name="Acme GmbH") -> Vendor:
    vendor = Vendor(
        id=uuid.uuid4(),
        organization_id=seeded["workspace_a_id"],
        name=name,
        name_norm=name.lower(),
    )
    async with session_factory() as session:
        session.add(vendor)
        await session.commit()
    return vendor


async def seed_invoice_row(
    session_factory,
    seeded,
    vendor: Vendor,
    *,
    invoice_number: str,
    total_amount: str,
    invoice_date: date,
    content_hash: str | None = None,
    organization_id: uuid.UUID | None = None,
) -> Invoice:
    organization_id = organization_id or seeded["workspace_a_id"]
    document = DocumentFile(
        id=uuid.uuid4(),
        organization_id=organization_id,
        storage_path=f"{organization_id}/2026/11/{uuid.uuid4()}_{invoice_number}.pdf",
        original_filename=f"{invoice_number}.pdf",
        mime_type="application/pdf",
        file_size_bytes=2048,
        content_hash=content_hash or uuid.uuid4().hex,
        uploaded_by_id=seeded["user_a_id"],
    )
    invoice = Invoice(
        id=uuid.uuid4(),
        organization_id=organization_id,
        document_id=document.id,
        vendor_id=vendor.id,
        invoice_number=invoice_number,
        total_amount=Decimal(total_amount),
        invoice_date=invoice_date,
        status=InvoiceStatus.NEEDS_REVIEW,
    )
    async with session_factory() as session:
        session.add_all([document, invoice])
        await session.commit()
    return invoice


def candidate_invoice(
    seeded, vendor: Vendor, *, invoice_number: str, total_amount: str, invoice_date: date
) -> Invoice:
    """Unsaved invoice under evaluation (has an id so self-exclusion works)."""
    return Invoice(
        id=uuid.uuid4(),
        organization_id=seeded["workspace_a_id"],
        vendor_id=vendor.id,
        invoice_number=invoice_number,
        total_amount=Decimal(total_amount),
        invoice_date=invoice_date,
    )


async def test_exact_metadata_match_flags_duplicate(
    seeded, session_factory
) -> None:
    vendor = await seed_vendor(session_factory, seeded)
    await seed_invoice_row(
        session_factory,
        seeded,
        vendor,
        invoice_number="INV-1042",
        total_amount="1488.00",
        invoice_date=date(2026, 11, 1),
    )
    candidate = candidate_invoice(
        seeded,
        vendor,
        invoice_number="INV-1042",
        total_amount="1488.00",
        invoice_date=date(2026, 11, 5),
    )

    async with session_factory() as db:
        is_duplicate, reasons = await check_duplicates(db, candidate, None)

    assert is_duplicate is True
    rules = {reason["rule"] for reason in reasons}
    assert "exact_metadata" in rules
    assert all(r["invoice_id"] for r in reasons)


async def test_three_day_proximity_flags_near_duplicate(
    seeded, session_factory
) -> None:
    vendor = await seed_vendor(session_factory, seeded)
    await seed_invoice_row(
        session_factory,
        seeded,
        vendor,
        invoice_number="INV-1042",
        total_amount="1488.00",
        invoice_date=date(2026, 11, 1),
    )
    # Same vendor + total, ±3 days, but a DIFFERENT invoice number
    candidate = candidate_invoice(
        seeded,
        vendor,
        invoice_number="INV-1043",
        total_amount="1488.00",
        invoice_date=date(2026, 11, 3),
    )

    async with session_factory() as db:
        is_duplicate, reasons = await check_duplicates(db, candidate, None)

    assert is_duplicate is True
    assert "near_duplicate_window" in {reason["rule"] for reason in reasons}


async def test_outside_window_or_different_total_is_clean(
    seeded, session_factory
) -> None:
    vendor = await seed_vendor(session_factory, seeded)
    await seed_invoice_row(
        session_factory,
        seeded,
        vendor,
        invoice_number="INV-1042",
        total_amount="1488.00",
        invoice_date=date(2026, 11, 1),
    )

    # Same number but different total → not an exact metadata match
    different_total = candidate_invoice(
        seeded, vendor, invoice_number="INV-1042", total_amount="99.00",
        invoice_date=date(2026, 11, 1),
    )
    # Same total but 10 days later and a different number → outside the window
    outside_window = candidate_invoice(
        seeded, vendor, invoice_number="INV-2000", total_amount="1488.00",
        invoice_date=date(2026, 11, 11),
    )

    async with session_factory() as db:
        dup_total, reasons_total = await check_duplicates(db, different_total, None)
        dup_window, reasons_window = await check_duplicates(db, outside_window, None)

    assert dup_total is False and reasons_total == []
    assert dup_window is False and reasons_window == []


async def test_cross_tenant_identical_invoice_not_flagged(
    seeded, session_factory
) -> None:
    """Same vendor name + number + total in ANOTHER tenant must not flag."""
    # Vendor in workspace A with a persisted invoice
    vendor_a = await seed_vendor(session_factory, seeded)
    await seed_invoice_row(
        session_factory,
        seeded,
        vendor_a,
        invoice_number="INV-1042",
        total_amount="1488.00",
        invoice_date=date(2026, 11, 1),
    )

    # Mirror invoice in workspace B (its own vendor row + document)
    vendor_b = Vendor(
        id=uuid.uuid4(),
        organization_id=seeded["workspace_b_id"],
        name="Acme GmbH",
        name_norm="acme-gmbh",
    )
    mirror_document = DocumentFile(
        id=uuid.uuid4(),
        organization_id=seeded["workspace_b_id"],
        storage_path=f"{seeded['workspace_b_id']}/2026/11/{uuid.uuid4()}_mirror.pdf",
        original_filename="mirror.pdf",
        mime_type="application/pdf",
        file_size_bytes=2048,
        content_hash=uuid.uuid4().hex,
        uploaded_by_id=seeded["user_b_id"],
    )
    mirror = Invoice(
        id=uuid.uuid4(),
        organization_id=seeded["workspace_b_id"],
        document_id=mirror_document.id,
        vendor_id=vendor_b.id,
        invoice_number="INV-1042",
        total_amount=Decimal("1488.00"),
        invoice_date=date(2026, 11, 1),
    )
    async with session_factory() as session:
        session.add_all([vendor_b, mirror_document, mirror])
        await session.commit()

    async with session_factory() as db:
        is_duplicate, reasons = await check_duplicates(db, mirror, None)

    assert is_duplicate is False
    assert reasons == []


async def test_exact_file_hash_flags_duplicate(seeded, session_factory) -> None:
    shared_hash = "f" * 64
    vendor = await seed_vendor(session_factory, seeded)
    original = await seed_invoice_row(
        session_factory,
        seeded,
        vendor,
        invoice_number="INV-1042",
        total_amount="1488.00",
        invoice_date=date(2026, 11, 1),
        content_hash=shared_hash,
    )
    candidate = candidate_invoice(
        seeded, vendor, invoice_number="INV-9999", total_amount="10.00",
        invoice_date=date(2026, 12, 1),
    )

    async with session_factory() as db:
        is_duplicate, reasons = await check_duplicates(db, candidate, shared_hash)

    assert is_duplicate is True
    assert "exact_file" in {reason["rule"] for reason in reasons}
    file_reason = next(r for r in reasons if r["rule"] == "exact_file")
    assert file_reason["invoice_id"] == str(original.id)
