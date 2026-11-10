"""Phase 6 step 2 tests: XLSX/CSV exports (structure, MIME, tenant boundary,
filters) and the dashboard summary aggregation (counts, FX-converted spend)."""

import io
import uuid
from datetime import date
from decimal import Decimal

import openpyxl
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models import Invoice, InvoiceStatus
from app.modules.dashboard.fx import FX_TO_USD
from tests.conftest import auth_headers
from tests.test_vendors import seed_vendor_row, seed_vendor_invoice


def ws_headers(seeded, user: str = "user_a_id") -> dict[str, str]:
    return {
        **auth_headers(seeded[user], "user@example.com"),
        "X-Workspace-ID": str(seeded["workspace_a_id"]),
    }


@pytest.fixture
async def export_data(seeded, session_factory):
    """Vendor + two invoices in workspace A (one approved, one in review),
    one approved invoice in workspace B that must never leak."""
    vendor = await seed_vendor_row(session_factory, seeded, name="Acme GmbH")
    approved = await seed_vendor_invoice(
        session_factory, seeded, vendor, status=InvoiceStatus.APPROVED,
        total="1488.00", invoice_date=date(2026, 10, 28),
    )
    await seed_vendor_invoice(
        session_factory, seeded, vendor, status=InvoiceStatus.NEEDS_REVIEW,
        total="50.00", invoice_date=date(2026, 11, 1),
    )
    foreign = await seed_vendor_invoice(
        session_factory,
        seeded,
        await seed_vendor_row(
            session_factory, seeded, name="B Corp", organization_id=seeded["workspace_b_id"]
        ),
        status=InvoiceStatus.APPROVED,
        total="999.00",
        invoice_date=date(2026, 11, 2),
        organization_id=seeded["workspace_b_id"],
    )
    return {"vendor": vendor, "approved": approved, "foreign": foreign}


# ── XLSX export ──────────────────────────────────────────────────────────


async def test_xlsx_export_structure_and_mime(
    client: AsyncClient, seeded, export_data
) -> None:
    response = await client.get("/api/v1/exports/invoices.xlsx", headers=ws_headers(seeded))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "attachment" in response.headers.get("content-disposition", "")
    assert "invoices_export_" in response.headers.get("content-disposition", "")

    workbook = openpyxl.load_workbook(io.BytesIO(response.content))
    sheet = workbook.active

    # Header row: exact columns, styled
    expected_headers = [
        "Invoice #", "Vendor Name", "Invoice Date", "Due Date", "Currency",
        "Subtotal", "Tax", "Shipping", "Total Amount", "Status",
        "Approved By", "Approved At",
    ]
    assert [cell.value for cell in sheet[1]] == expected_headers
    assert sheet["A1"].font.bold is True
    assert sheet["A1"].font.color.rgb.endswith("FFFFFF")
    assert sheet.auto_filter.ref is not None

    # Tenant boundary: workspace A's 2 invoices only — never org B's
    assert sheet.max_row == 3
    data_rows = list(sheet.iter_rows(min_row=2, values_only=True))
    assert {row[9] for row in data_rows} == {"approved", "needs_review"}
    assert all(row[1] == "Acme GmbH" for row in data_rows)
    assert all(row[8] != 999.0 for row in data_rows)

    # Money columns carry the '#,##0.00' presentation format
    total_cell = sheet["I2"]
    assert total_cell.number_format == "#,##0.00"
    # Date columns carry 'YYYY-MM-DD'
    assert sheet["C2"].number_format == "YYYY-MM-DD"


async def test_xlsx_export_respects_filters(
    client: AsyncClient, seeded, export_data
) -> None:
    response = await client.get(
        "/api/v1/exports/invoices.xlsx",
        headers=ws_headers(seeded),
        params={"status": "approved", "date_from": "2026-10-01", "date_to": "2026-10-31"},
    )
    assert response.status_code == 200
    sheet = openpyxl.load_workbook(io.BytesIO(response.content)).active
    assert sheet.max_row == 2  # only the approved October invoice
    assert sheet["J2"].value == "approved"

    invalid = await client.get(
        "/api/v1/exports/invoices.csv",
        headers=ws_headers(seeded),
        params={"status": "bogus"},
    )
    assert invalid.status_code == 422


# ── CSV export ───────────────────────────────────────────────────────────


async def test_csv_export_headers_and_tenant_boundary(
    client: AsyncClient, seeded, export_data
) -> None:
    response = await client.get("/api/v1/exports/invoices.csv", headers=ws_headers(seeded))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")

    text = response.content.decode("utf-8")
    lines = text.strip().splitlines()
    assert lines[0] == (
        "Invoice #,Vendor Name,Invoice Date,Due Date,Currency,Subtotal,Tax,"
        "Shipping,Total Amount,Status,Approved By,Approved At"
    )
    assert len(lines) == 3  # header + 2 workspace-A invoices
    assert "999.00" not in text  # org B's amount never leaks
    assert any("1488.00" in line for line in lines[1:])


# ── Dashboard summary ────────────────────────────────────────────────────


async def test_dashboard_summary_aggregation_and_fx(
    client: AsyncClient, seeded, session_factory
) -> None:
    vendor = await seed_vendor_row(session_factory, seeded, name="Acme GmbH")
    # Approved: EUR 100 (→ 108.00 USD at the static rate) + USD 50 → 158.00
    eur_invoice = await seed_vendor_invoice(
        session_factory, seeded, vendor, status=InvoiceStatus.APPROVED,
        total="100.00", invoice_date=date(2026, 10, 1),
    )
    usd_invoice = await seed_vendor_invoice(
        session_factory, seeded, vendor, status=InvoiceStatus.APPROVED,
        total="50.00", invoice_date=date(2026, 10, 2),
    )
    # Exercise FX conversion with explicit currencies on both invoices
    async with session_factory() as session:
        for invoice, currency in ((eur_invoice, "EUR"), (usd_invoice, "USD")):
            stored = await session.get(Invoice, invoice.id)
            stored.currency_code = currency
        await session.commit()

    await seed_vendor_invoice(
        session_factory, seeded, vendor, status=InvoiceStatus.NEEDS_REVIEW,
        total="77.00", invoice_date=date(2026, 11, 1),
    )
    await seed_vendor_invoice(
        session_factory, seeded, vendor, status=InvoiceStatus.FAILED,
        total="13.00", invoice_date=date(2026, 11, 2),
    )

    response = await client.get("/api/v1/dashboard/summary", headers=ws_headers(seeded))
    assert response.status_code == 200
    body = response.json()

    # Workspace A seeds with GBP defaults (conftest)
    assert body["base_currency"] == "GBP"
    assert body["total_invoices_count"] == 4
    assert body["pending_review_count"] == 1
    assert body["approved_invoices_count"] == 2
    assert body["failed_invoices_count"] == 1

    # Each conversion quantizes to pennies before summing (same as the service)
    eur_to_gbp = (Decimal("100.00") * FX_TO_USD["EUR"] / FX_TO_USD["GBP"]).quantize(Decimal("0.01"))
    usd_to_gbp = (Decimal("50.00") * FX_TO_USD["USD"] / FX_TO_USD["GBP"]).quantize(Decimal("0.01"))
    expected = eur_to_gbp + usd_to_gbp
    assert Decimal(body["total_approved_spend"]) == expected
    assert body["unpriced_currencies"] == []

    assert len(body["recent_activity"]) == 4
    assert any(a["vendor_name"] == "Acme GmbH" for a in body["recent_activity"])

    assert len(body["spend_by_vendor"]) == 1
    assert body["spend_by_vendor"][0]["vendor_name"] == "Acme GmbH"
    assert Decimal(body["spend_by_vendor"][0]["total_spend"]) == expected
    assert body["spend_by_vendor"][0]["currency"] == "GBP"


async def test_dashboard_summary_isolated_from_other_tenant(
    client: AsyncClient, seeded, session_factory
) -> None:
    vendor_b = await seed_vendor_row(
        session_factory, seeded, name="B Corp", organization_id=seeded["workspace_b_id"]
    )
    await seed_vendor_invoice(
        session_factory, seeded, vendor_b, status=InvoiceStatus.APPROVED,
        total="500.00", invoice_date=date(2026, 11, 3),
        organization_id=seeded["workspace_b_id"],
    )

    response = await client.get("/api/v1/dashboard/summary", headers=ws_headers(seeded))
    assert response.status_code == 200
    body = response.json()
    assert body["total_invoices_count"] == 0
    assert body["total_approved_spend"] is None
    assert body["spend_by_vendor"] == []
