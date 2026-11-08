"""Multi-signal duplicate detection (PRD FR: duplicate warnings, docs/ARCHITECTURE.md §5).

Three signals, all scoped to the current organization and excluding the
invoice under evaluation:

1. `exact_file`            — another invoice whose document has the same
                             content hash (byte-identical file).
2. `exact_metadata`        — same vendor + invoice_number + total_amount.
3. `near_duplicate_window` — same vendor + total_amount with an invoice date
                             within ±3 days AND a different (or missing)
                             invoice number.

The detector only FLAGS (`is_possible_duplicate=True` + structured reasons);
it never blocks ingestion or deletes anything — credit notes legitimately
reuse numbers, so the human decides (docs/PRD.md §11).
"""

import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DocumentFile, Invoice

logger = logging.getLogger(__name__)

NEAR_DUPLICATE_WINDOW_DAYS = 3
_MAX_REASONS_PER_RULE = 5


def _exclude_self(statement, invoice: Invoice):
    if invoice.id is not None:
        statement = statement.where(Invoice.id != invoice.id)
    return statement


async def check_duplicates(
    db: AsyncSession, invoice: Invoice, file_hash: Optional[str]
) -> tuple[bool, list[dict]]:
    """Evaluate all duplicate signals for one invoice.

    Returns `(is_possible_duplicate, duplicate_reasons)` where reasons are
    JSON-safe dicts: {"rule", "invoice_id", "details"}.
    """
    reasons: list[dict] = []

    # ── Check 1: exact file duplicate (same content hash) ──
    if file_hash:
        statement = _exclude_self(
            select(Invoice.id, Invoice.invoice_number)
            .join(DocumentFile, DocumentFile.id == Invoice.document_id)
            .where(
                Invoice.organization_id == invoice.organization_id,
                DocumentFile.content_hash == file_hash,
            ),
            invoice,
        ).limit(_MAX_REASONS_PER_RULE)
        for row_id, invoice_number in (await db.execute(statement)).all():
            reasons.append(
                {
                    "rule": "exact_file",
                    "invoice_id": str(row_id),
                    "details": {
                        "content_hash": file_hash,
                        "other_invoice_number": invoice_number,
                    },
                }
            )

    # ── Check 2: exact metadata duplicate ──
    if (
        invoice.vendor_id is not None
        and invoice.invoice_number
        and invoice.total_amount is not None
    ):
        statement = _exclude_self(
            select(Invoice.id, Invoice.invoice_date)
            .where(
                Invoice.organization_id == invoice.organization_id,
                Invoice.vendor_id == invoice.vendor_id,
                Invoice.invoice_number == invoice.invoice_number,
                Invoice.total_amount == invoice.total_amount,
            ),
            invoice,
        ).limit(_MAX_REASONS_PER_RULE)
        for row_id, invoice_date in (await db.execute(statement)).all():
            reasons.append(
                {
                    "rule": "exact_metadata",
                    "invoice_id": str(row_id),
                    "details": {
                        "invoice_number": invoice.invoice_number,
                        "total_amount": str(invoice.total_amount),
                        "other_invoice_date": invoice_date.isoformat() if invoice_date else None,
                    },
                }
            )

    # ── Check 3: near duplicate — same vendor/total, date within ±3 days,
    #    different or missing invoice number ──
    if (
        invoice.vendor_id is not None
        and invoice.total_amount is not None
        and invoice.invoice_date is not None
    ):
        window_start = invoice.invoice_date - timedelta(days=NEAR_DUPLICATE_WINDOW_DAYS)
        window_end = invoice.invoice_date + timedelta(days=NEAR_DUPLICATE_WINDOW_DAYS)
        number_predicate = (
            or_(
                Invoice.invoice_number.is_(None),
                Invoice.invoice_number != invoice.invoice_number,
            )
            if invoice.invoice_number
            else None
        )
        predicates = [
            Invoice.organization_id == invoice.organization_id,
            Invoice.vendor_id == invoice.vendor_id,
            Invoice.total_amount == invoice.total_amount,
            Invoice.invoice_date >= window_start,
            Invoice.invoice_date <= window_end,
        ]
        if number_predicate is not None:
            predicates.append(number_predicate)
        statement = _exclude_self(
            select(Invoice.id, Invoice.invoice_number).where(and_(*predicates)),
            invoice,
        ).limit(_MAX_REASONS_PER_RULE)
        for row_id, invoice_number in (await db.execute(statement)).all():
            reasons.append(
                {
                    "rule": "near_duplicate_window",
                    "invoice_id": str(row_id),
                    "details": {
                        "window_days": NEAR_DUPLICATE_WINDOW_DAYS,
                        "invoice_date": invoice.invoice_date.isoformat(),
                        "other_invoice_number": invoice_number,
                        "total_amount": str(invoice.total_amount),
                    },
                }
            )

    if reasons:
        logger.info(
            "duplicate signals for invoice %s: %s",
            getattr(invoice, "id", None) or "unsaved",
            [reason["rule"] for reason in reasons],
        )
    return (len(reasons) > 0, reasons)
