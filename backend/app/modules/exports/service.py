"""Export builders: shared filtered row query, styled XLSX, streaming CSV.

Both formats emit identical columns and the same tenant-scoped row set.
Money is written at full stored precision (penny-exact, ADR-007); the XLSX
presentation layer applies '#,##0.00' display formatting on top.
"""

import csv
import io
import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Invoice, InvoiceStatus, Vendor

logger = logging.getLogger(__name__)

MAX_EXPORT_ROWS = 10_000  # sync export cap; async bulk export is post-MVP

EXPORT_COLUMNS = [
    "Invoice #",
    "Vendor Name",
    "Invoice Date",
    "Due Date",
    "Currency",
    "Subtotal",
    "Tax",
    "Shipping",
    "Total Amount",
    "Status",
    "Approved By",
    "Approved At",
]

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MONEY_FORMAT = "#,##0.00"
DATE_FORMAT = "YYYY-MM-DD"
DATETIME_FORMAT = "YYYY-MM-DD HH:MM:SS"


def parse_status_filter(status_param: str | None) -> list[InvoiceStatus]:
    statuses: list[InvoiceStatus] = []
    for part in (status_param or "").split(","):
        part = part.strip().lower()
        if not part:
            continue
        try:
            statuses.append(InvoiceStatus(part))
        except ValueError as exc:
            raise ValueError(f"Unknown invoice status: '{part}'.") from exc
    return statuses


def build_export_predicates(
    org_id: uuid.UUID,
    *,
    statuses: list[InvoiceStatus],
    vendor_id: uuid.UUID | None,
    date_from: date | None,
    date_to: date | None,
) -> list[Any]:
    predicates: list[Any] = [Invoice.organization_id == org_id]
    if statuses:
        predicates.append(Invoice.status.in_(statuses))
    if vendor_id is not None:
        predicates.append(Invoice.vendor_id == vendor_id)
    if date_from is not None:
        predicates.append(Invoice.invoice_date >= date_from)
    if date_to is not None:
        predicates.append(Invoice.invoice_date <= date_to)
    return predicates


async def fetch_export_rows(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    statuses: list[InvoiceStatus],
    vendor_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    """Tenant-scoped rows (newest first), vendor name joined, capped."""
    statement = (
        select(Invoice, Vendor.name)
        .outerjoin(Vendor, Vendor.id == Invoice.vendor_id)
        .where(
            *build_export_predicates(
                org_id,
                statuses=statuses,
                vendor_id=vendor_id,
                date_from=date_from,
                date_to=date_to,
            )
        )
        .order_by(Invoice.invoice_date.desc().nulls_last(), Invoice.created_at.desc())
        .limit(MAX_EXPORT_ROWS)
    )
    rows = (await session.execute(statement)).all()
    return [
        {
            "invoice_number": invoice.invoice_number,
            "vendor_name": vendor_name,
            "invoice_date": invoice.invoice_date,
            "due_date": invoice.due_date,
            "currency": invoice.currency_code,
            "subtotal": invoice.subtotal_amount,
            "tax": invoice.tax_amount,
            "shipping": None,  # not modeled in the MVP schema
            "total": invoice.total_amount,
            "status": invoice.status.value,
            "approved_by": str(invoice.approved_by_id) if invoice.approved_by_id else None,
            "approved_at": invoice.approved_at,
        }
        for invoice, vendor_name in rows
    ]


def _display(value: Any) -> str:
    return "" if value is None else str(value)


def build_csv(rows: list[dict[str, Any]]) -> str:
    """UTF-8 CSV with identical headers; money at full stored precision."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        writer.writerow(
            [
                _display(row["invoice_number"]),
                _display(row["vendor_name"]),
                row["invoice_date"].isoformat() if row["invoice_date"] else "",
                row["due_date"].isoformat() if row["due_date"] else "",
                _display(row["currency"]),
                _display(row["subtotal"]),
                _display(row["tax"]),
                _display(row["shipping"]),
                _display(row["total"]),
                _display(row["status"]),
                _display(row["approved_by"]),
                row["approved_at"].isoformat() if row["approved_at"] else "",
            ]
        )
    return buffer.getvalue()


def build_xlsx(rows: list[dict[str, Any]]) -> bytes:
    """Styled workbook: navy header with white bold text, auto-filter,
    '#,##0.00' money columns, 'YYYY-MM-DD' dates, content-fit widths."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Invoices"

    header_fill = PatternFill(fill_type="solid", start_color="FF0F172A", end_color="FF0F172A")
    header_font = Font(color="FFFFFFFF", bold=True)
    header_alignment = Alignment(vertical="center")

    sheet.append(EXPORT_COLUMNS)
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_alignment

    money_columns = {6, 7, 8, 9}  # Subtotal, Tax, Shipping, Total Amount (1-based)
    date_columns = {3, 4}
    datetime_columns = {12}

    for row in rows:
        sheet.append(
            [
                row["invoice_number"],
                row["vendor_name"],
                row["invoice_date"],
                row["due_date"],
                row["currency"],
                float(row["subtotal"]) if row["subtotal"] is not None else None,
                float(row["tax"]) if row["tax"] is not None else None,
                float(row["shipping"]) if row["shipping"] is not None else None,
                float(row["total"]) if row["total"] is not None else None,
                row["status"],
                row["approved_by"],
                row["approved_at"],
            ]
        )

    for row_cells in sheet.iter_rows(min_row=2):
        for index, cell in enumerate(row_cells, start=1):
            if index in money_columns and isinstance(cell.value, (int, float, Decimal)):
                cell.number_format = MONEY_FORMAT
            elif index in date_columns and isinstance(cell.value, date):
                cell.number_format = DATE_FORMAT
            elif index in datetime_columns and isinstance(cell.value, datetime):
                cell.number_format = DATETIME_FORMAT

    # Auto-filter over the used range
    last_column = get_column_letter(len(EXPORT_COLUMNS))
    last_row = len(rows) + 1
    sheet.auto_filter.ref = f"A1:{last_column}{max(last_row, 1)}"

    # Content-fit widths (clamped 8–40)
    widths = [len(column) for column in EXPORT_COLUMNS]
    for row in sheet.iter_rows(min_row=2, values_only=True):
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(_display(value)))
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = min(max(width + 2, 8), 40)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def export_filename(extension: str, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d")
    return f"invoices_export_{stamp}.{extension}"
