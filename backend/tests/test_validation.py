"""Phase 5 deterministic validation engine tests (pure, no DB).

All cases build in-memory ORM objects — the engine is a pure function."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

from app.models import Invoice, InvoiceItem
from app.modules.validation.engine import validate_invoice_financials
from app.modules.validation.schemas import ValidationRuleCode, ValidationSeverity


def build_item(
    line_number: int = 1,
    quantity: str | None = "3",
    unit_price: str | None = "10.00",
    line_total: str | None = "30.00",
    description: str = "Consulting",
) -> InvoiceItem:
    return InvoiceItem(
        line_number=line_number,
        description=description,
        quantity=Decimal(quantity) if quantity is not None else None,
        unit_price=Decimal(unit_price) if unit_price is not None else None,
        line_total=Decimal(line_total) if line_total is not None else None,
    )


def build_invoice(**overrides) -> Invoice:
    base: dict = dict(
        invoice_number="INV-1042",
        invoice_date=date(2026, 11, 1),
        due_date=date(2026, 12, 1),
        currency_code="EUR",
        subtotal_amount=Decimal("30.00"),
        tax_amount=Decimal("6.00"),
        total_amount=Decimal("36.00"),
    )
    base.update(overrides)
    return Invoice(id=uuid.uuid4(), **base)


def codes(issues):
    return {issue.code for issue in issues}


# ── Line-item math ───────────────────────────────────────────────────────


def test_line_math_mismatch_triggers_rule() -> None:
    """qty=3 × 10.00 = 30.00, but line_total=35.00 → flagged."""
    invoice = build_invoice()
    items = [build_item(quantity="3", unit_price="10.00", line_total="35.00")]

    issues = validate_invoice_financials(invoice, items)

    assert ValidationRuleCode.MATH_LINE_ITEM_MISMATCH in codes(issues)
    issue = next(i for i in issues if i.code == ValidationRuleCode.MATH_LINE_ITEM_MISMATCH)
    assert issue.field == "line_items[1]"
    assert issue.details["expected_line_total"] == "30.00"
    assert issue.details["actual_line_total"] == "35.00"


def test_line_math_within_penny_tolerance_is_clean() -> None:
    """30.01 vs 30.00 → within the 0.02 tolerance → no issue."""
    invoice = build_invoice()
    items = [build_item(quantity="3", unit_price="10.00", line_total="30.01")]

    issues = validate_invoice_financials(invoice, items)

    assert ValidationRuleCode.MATH_LINE_ITEM_MISMATCH not in codes(issues)


def test_incomplete_line_item_skips_math_rule() -> None:
    invoice = build_invoice()
    items = [build_item(quantity=None, unit_price="10.00", line_total="999.00")]

    issues = validate_invoice_financials(invoice, items)

    assert ValidationRuleCode.MATH_LINE_ITEM_MISMATCH not in codes(issues)


# ── Subtotal / total math ────────────────────────────────────────────────


def test_total_math_mismatch_triggers_rule() -> None:
    """subtotal=100.00 + tax=10.00 = 110.00, but total=120.00 → flagged."""
    invoice = build_invoice(
        subtotal_amount=Decimal("100.00"),
        tax_amount=Decimal("10.00"),
        total_amount=Decimal("120.00"),
        invoice_number=None,  # keep critical-field noise out of this assertion
        invoice_date=None,
        due_date=None,
    )

    issues = validate_invoice_financials(invoice, items=[])

    assert ValidationRuleCode.MATH_TOTAL_MISMATCH in codes(issues)
    issue = next(i for i in issues if i.code == ValidationRuleCode.MATH_TOTAL_MISMATCH)
    assert issue.field == "total_amount"
    assert issue.severity == ValidationSeverity.ERROR
    assert issue.details["expected_total"] == "110.00"
    assert issue.details["actual_total"] == "120.00"


def test_subtotal_mismatch_triggers_rule() -> None:
    invoice = build_invoice(subtotal_amount=Decimal("50.00"), total_amount=None, due_date=None)
    items = [build_item(line_total="30.00")]

    issues = validate_invoice_financials(invoice, items)

    assert ValidationRuleCode.MATH_SUBTOTAL_MISMATCH in codes(issues)
    issue = next(i for i in issues if i.code == ValidationRuleCode.MATH_SUBTOTAL_MISMATCH)
    assert issue.details["expected_subtotal"] == "30.00"


# ── Clean invoice ────────────────────────────────────────────────────────


def test_clean_invoice_produces_zero_issues() -> None:
    """3 × 10 = 30; subtotal 30 + tax 6 = total 36; dates consistent → []."""
    invoice = build_invoice()
    items = [build_item()]

    issues = validate_invoice_financials(invoice, items)

    assert issues == []


# ── Missing critical fields ──────────────────────────────────────────────


def test_missing_critical_fields_flagged_as_errors() -> None:
    invoice = build_invoice(invoice_number=None, total_amount=None, invoice_date=None)

    issues = validate_invoice_financials(invoice, items=[])

    missing = [i for i in issues if i.code == ValidationRuleCode.MISSING_CRITICAL_FIELD]
    assert {i.field for i in missing} == {"invoice_number", "total_amount", "invoice_date"}
    assert all(i.severity == ValidationSeverity.ERROR for i in missing)


# ── Date consistency ─────────────────────────────────────────────────────


def test_due_date_before_invoice_date_flagged() -> None:
    invoice = build_invoice(invoice_date=date(2026, 11, 10), due_date=date(2026, 11, 1))

    issues = validate_invoice_financials(invoice, items=[])

    assert ValidationRuleCode.DATE_INCONSISTENCY in codes(issues)


def test_future_invoice_date_flagged() -> None:
    invoice = build_invoice(invoice_date=date.today() + timedelta(days=30))

    issues = validate_invoice_financials(invoice, items=[])

    assert ValidationRuleCode.DATE_INCONSISTENCY in codes(issues)


def test_invoice_date_within_grace_window_is_clean() -> None:
    invoice = build_invoice(invoice_date=date.today() + timedelta(days=1))

    issues = validate_invoice_financials(invoice, items=[])

    assert ValidationRuleCode.DATE_INCONSISTENCY not in codes(issues)


def test_engine_never_mutates_amounts() -> None:
    """The engine reports mismatches but must not 'fix' any value in place."""
    invoice = build_invoice(subtotal_amount=Decimal("100.00"), total_amount=Decimal("120.00"))
    items = [build_item(quantity="3", unit_price="10.00", line_total="35.00")]

    validate_invoice_financials(invoice, items)

    assert invoice.subtotal_amount == Decimal("100.00")
    assert invoice.total_amount == Decimal("120.00")
    assert items[0].line_total == Decimal("35.00")
    assert items[0].quantity == Decimal("3")
