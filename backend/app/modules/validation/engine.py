"""Deterministic financial rules engine.

Pure function over ORM objects — no DB, no provider calls, fully
deterministic. Ground rules:

- All arithmetic uses Decimal (ADR-007); comparisons tolerate ±0.02 for
  penny-rounding discrepancies in vendor documents.
- The engine NEVER mutates extracted amounts to force equations to balance.
  Discrepancies are reported as structured issues; humans decide.
- Incomplete data (e.g. a line item missing quantity) skips that specific
  rule rather than guessing; absence is covered by MISSING_CRITICAL_FIELD.

Discount/shipping are not modeled in the MVP schema, so the total equation is
`subtotal + tax == total` (discount/shipping default to 0 and are noted in
the rule's details when relevant).
"""

from datetime import date, timedelta
from decimal import Decimal

from app.models import Invoice, InvoiceItem
from app.modules.validation.schemas import (
    ValidationIssue,
    ValidationRuleCode,
    ValidationSeverity,
)

TOLERANCE = Decimal("0.02")
FUTURE_DATE_GRACE_DAYS = 1

_TWO_PLACES = Decimal("0.01")


def _money(value: Decimal) -> str:
    return str(value.quantize(_TWO_PLACES)) if value is not None else "null"


def _subtracted(*values: Decimal | None) -> Decimal:
    result = Decimal("0")
    for value in values:
        if value is not None:
            result += value
    return result


def _check_missing_critical_fields(invoice: Invoice, issues: list[ValidationIssue]) -> None:
    for field in ("invoice_number", "total_amount", "invoice_date"):
        if getattr(invoice, field) is None:
            issues.append(
                ValidationIssue(
                    code=ValidationRuleCode.MISSING_CRITICAL_FIELD,
                    field=field,
                    message=f"Critical field '{field}' is missing after extraction.",
                    severity=ValidationSeverity.ERROR,
                )
            )


def _check_line_item_math(invoice: Invoice, items: list[InvoiceItem], issues: list[ValidationIssue]) -> None:
    for item in items:
        if item.quantity is None or item.unit_price is None or item.line_total is None:
            continue  # incomplete line — critical-field rules cover the header
        expected = item.quantity * item.unit_price
        if abs(expected - item.line_total) > TOLERANCE:
            issues.append(
                ValidationIssue(
                    code=ValidationRuleCode.MATH_LINE_ITEM_MISMATCH,
                    field=f"line_items[{item.line_number}]",
                    message=(
                        f"Line {item.line_number}: quantity × unit_price "
                        f"({_money(expected)}) does not match line_total "
                        f"({_money(item.line_total)})."
                    ),
                    severity=ValidationSeverity.WARNING,
                    details={
                        "quantity": str(item.quantity),
                        "unit_price": str(item.unit_price),
                        "expected_line_total": _money(expected),
                        "actual_line_total": _money(item.line_total),
                    },
                )
            )


def _check_subtotal_math(invoice: Invoice, items: list[InvoiceItem], issues: list[ValidationIssue]) -> None:
    line_totals = [item.line_total for item in items if item.line_total is not None]
    if invoice.subtotal_amount is None or not line_totals:
        return
    items_total = sum(line_totals, Decimal("0"))
    if abs(items_total - invoice.subtotal_amount) > TOLERANCE:
        issues.append(
            ValidationIssue(
                code=ValidationRuleCode.MATH_SUBTOTAL_MISMATCH,
                field="subtotal_amount",
                message=(
                    f"Sum of line totals ({_money(items_total)}) does not match "
                    f"the invoice subtotal ({_money(invoice.subtotal_amount)})."
                ),
                severity=ValidationSeverity.ERROR,
                details={
                    "expected_subtotal": _money(items_total),
                    "actual_subtotal": _money(invoice.subtotal_amount),
                    "line_total_count": len(line_totals),
                },
            )
        )


def _check_total_math(invoice: Invoice, issues: list[ValidationIssue]) -> None:
    if invoice.subtotal_amount is None or invoice.total_amount is None:
        return
    # discount and shipping are not modeled in the MVP schema → treated as 0.
    expected_total = invoice.subtotal_amount + (invoice.tax_amount or Decimal("0"))
    if abs(expected_total - invoice.total_amount) > TOLERANCE:
        issues.append(
            ValidationIssue(
                code=ValidationRuleCode.MATH_TOTAL_MISMATCH,
                field="total_amount",
                message=(
                    f"subtotal + tax ({_money(expected_total)}) does not match "
                    f"the invoice total ({_money(invoice.total_amount)})."
                ),
                severity=ValidationSeverity.ERROR,
                details={
                    "expected_total": _money(expected_total),
                    "actual_total": _money(invoice.total_amount),
                    "subtotal": _money(invoice.subtotal_amount),
                    "tax_amount": _money(invoice.tax_amount),
                },
            )
        )


def _check_dates(invoice: Invoice, issues: list[ValidationIssue]) -> None:
    if invoice.due_date is not None and invoice.invoice_date is not None:
        if invoice.due_date < invoice.invoice_date:
            issues.append(
                ValidationIssue(
                    code=ValidationRuleCode.DATE_INCONSISTENCY,
                    field="due_date",
                    message=(
                        f"Due date ({invoice.due_date.isoformat()}) precedes the "
                        f"invoice date ({invoice.invoice_date.isoformat()})."
                    ),
                    severity=ValidationSeverity.WARNING,
                    details={
                        "invoice_date": invoice.invoice_date.isoformat(),
                        "due_date": invoice.due_date.isoformat(),
                    },
                )
            )
    if invoice.invoice_date is not None:
        horizon = date.today() + timedelta(days=FUTURE_DATE_GRACE_DAYS)
        if invoice.invoice_date > horizon:
            issues.append(
                ValidationIssue(
                    code=ValidationRuleCode.DATE_INCONSISTENCY,
                    field="invoice_date",
                    message=(
                        f"Invoice date ({invoice.invoice_date.isoformat()}) is more "
                        f"than {FUTURE_DATE_GRACE_DAYS} day(s) in the future."
                    ),
                    severity=ValidationSeverity.WARNING,
                    details={
                        "invoice_date": invoice.invoice_date.isoformat(),
                        "today": date.today().isoformat(),
                    },
                )
            )


def validate_invoice_financials(
    invoice: Invoice, items: list[InvoiceItem]
) -> list[ValidationIssue]:
    """Run every deterministic rule; returns structured issues (never mutates)."""
    issues: list[ValidationIssue] = []
    _check_missing_critical_fields(invoice, issues)
    _check_line_item_math(invoice, items, issues)
    _check_subtotal_math(invoice, items, issues)
    _check_total_math(invoice, issues)
    _check_dates(invoice, issues)
    return issues
