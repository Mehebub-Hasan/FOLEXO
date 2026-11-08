"""Schemas for the deterministic financial rules engine.

Severity policy:
- ERROR: the document cannot be trusted for payment without human correction
  (missing critical fields, totals that do not balance).
- WARNING: plausibly fine (penny rounding, odd dates) but worth a human glance.
"""

import enum
from typing import Any

from pydantic import BaseModel


class ValidationSeverity(str, enum.Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class ValidationRuleCode(str, enum.Enum):
    MATH_LINE_ITEM_MISMATCH = "MATH_LINE_ITEM_MISMATCH"  # qty * unit_price != line_total
    MATH_SUBTOTAL_MISMATCH = "MATH_SUBTOTAL_MISMATCH"  # sum(line_total) != subtotal
    MATH_TOTAL_MISMATCH = "MATH_TOTAL_MISMATCH"  # subtotal - discount + tax + shipping != total
    DATE_INCONSISTENCY = "DATE_INCONSISTENCY"  # due < invoice date, or invoice date in the future
    MISSING_CRITICAL_FIELD = "MISSING_CRITICAL_FIELD"  # invoice_number / total_amount / invoice_date


class ValidationIssue(BaseModel):
    code: ValidationRuleCode
    field: str
    message: str
    severity: ValidationSeverity
    details: dict[str, Any] | None = None
