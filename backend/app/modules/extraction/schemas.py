"""Normalized extraction schemas shared by all providers.

`ExtractedInvoiceData` is the pipeline's lingua franca: Azure maps its
`prebuilt-invoice` response into it, OpenAI structured output is validated
against it (via a JSON-schema-safe mirror), and the orchestrator persists it
into `extraction_runs.normalized_output` + invoice/invoice_items columns.

Money is Decimal (ADR-007); coordinates are normalized 0–1 polygons
(docs/ARCHITECTURE.md §7).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class ExtractedLineItem(BaseModel):
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None
    sku: str | None = None
    page_number: int | None = None
    confidence: float | None = None
    # Normalized polygon [[x, y], ...] (0–1, top-left origin)
    bounding_box: list[list[float]] | None = None


class ExtractedInvoiceData(BaseModel):
    vendor_name: str | None = None
    vendor_tax_id: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str = "USD"
    subtotal: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None
    line_items: list[ExtractedLineItem] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    # Coordinate maps for core header fields, keyed by field
    # ("invoice_number", "total_amount", ...): {"unit": "normalized",
    # "page_number": n, "polygon": [[x, y], ...]}.
    bounding_boxes: dict[str, Any] = Field(default_factory=dict)


class ExtractionError(RuntimeError):
    """A provider failed (unreachable, auth, malformed response)."""


class ExtractorConfigurationError(ExtractionError):
    """The provider's credentials/endpoint are not configured."""
