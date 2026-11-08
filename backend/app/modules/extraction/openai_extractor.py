"""OpenAI structured-output normalizer — Stage B conditional fallback (ADR-003).

Runs ONLY when Stage A's confidence is low or critical fields are missing.
The model receives the OCR text (Azure's markdown `content`) plus Stage A's
normalized output and repairs/normalizes it — it must never fabricate values:
the schema allows null everywhere and the system prompt forbids invention.

Output uses structured outputs (Pydantic schema → JSON schema) via
`chat.completions.parse`. LLM self-reported confidence is NOT trusted: the
carried Stage A confidence flows through, and the orchestrator forces
`needs_review` regardless.
"""

import base64
import logging
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import settings
from app.modules.extraction.base import BaseInvoiceExtractor
from app.modules.extraction.schemas import (
    ExtractedInvoiceData,
    ExtractedLineItem,
    ExtractionError,
    ExtractorConfigurationError,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You normalize invoice extraction data for an accounts-payable system. "
    "You receive OCR text from the invoice and the primary extractor's JSON output. "
    "Fix misspellings, normalize dates to ISO 8601, normalize currency codes to ISO 4217, "
    "and reconcile arithmetic. Use ONLY values supported by the OCR text or the primary "
    "output. Return null for anything not present — NEVER fabricate numbers, dates, names, "
    "or line items. Line items must match the document."
)


class OpenAILineItem(BaseModel):
    """JSON-schema-safe mirror of ExtractedLineItem (floats, no coordinate maps)."""

    description: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    line_total: float | None = None
    sku: str | None = None


class OpenAINormalizedInvoice(BaseModel):
    vendor_name: str | None = None
    vendor_tax_id: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str = "USD"
    subtotal: float | None = None
    tax_amount: float | None = None
    total_amount: float | None = None
    line_items: list[OpenAILineItem] = Field(default_factory=list)


def _decimal(value: float | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


class OpenAIFallbackExtractor(BaseInvoiceExtractor):
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any = None,
    ) -> None:
        resolved_key = api_key or settings.openai_api_key
        if client is None and not resolved_key:
            raise ExtractorConfigurationError(
                "OpenAI is not configured (OPENAI_API_KEY)"
            )
        if client is not None:
            self._client = client
        else:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=resolved_key)
        self._model = model or settings.openai_fallback_model

    @classmethod
    def from_settings(cls) -> "OpenAIFallbackExtractor":
        return cls(api_key=settings.openai_api_key, model=settings.openai_fallback_model)

    async def extract(
        self,
        document_bytes: bytes,
        mime_type: str,
        context: dict[str, Any] | None = None,
    ) -> ExtractedInvoiceData:
        context = context or {}
        ocr_text = context.get("ocr_text")
        azure_output = context.get("azure_extraction")

        content_parts: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "Primary extractor output (JSON, may be incomplete or low-confidence):\n"
                    f"{azure_output!r}\n\n"
                    f"OCR text from the invoice document:\n{ocr_text or '(unavailable)'}"
                ),
            }
        ]
        # Photos of invoices carry more signal as images than as raw bytes text.
        if mime_type.startswith("image/"):
            encoded = base64.b64encode(document_bytes).decode("ascii")
            content_parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                }
            )

        try:
            response = await self._client.chat.completions.parse(
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": content_parts},
                ],
                response_model=OpenAINormalizedInvoice,
            )
        except Exception as exc:
            raise ExtractionError(f"OpenAI fallback failed: {exc}") from exc

        parsed = getattr(response.choices[0].message, "parsed", None)
        if parsed is None:
            raise ExtractionError("OpenAI fallback returned no structured output")

        carried_confidence = 0.0
        if isinstance(azure_output, dict):
            carried_confidence = float(azure_output.get("confidence_score") or 0.0)

        return self._to_schema(parsed, carried_confidence)

    def _to_schema(self, parsed: OpenAINormalizedInvoice, carried_confidence: float) -> ExtractedInvoiceData:
        currency = (parsed.currency or "USD").strip().upper()[:3] or "USD"
        return ExtractedInvoiceData(
            vendor_name=parsed.vendor_name,
            vendor_tax_id=parsed.vendor_tax_id,
            invoice_number=parsed.invoice_number,
            invoice_date=parsed.invoice_date,
            due_date=parsed.due_date,
            currency=currency,
            subtotal=_decimal(parsed.subtotal),
            tax_amount=_decimal(parsed.tax_amount),
            total_amount=_decimal(parsed.total_amount),
            line_items=[
                ExtractedLineItem(
                    description=item.description,
                    quantity=_decimal(item.quantity),
                    unit_price=_decimal(item.unit_price),
                    line_total=_decimal(item.line_total),
                    sku=item.sku,
                )
                for item in parsed.line_items
            ],
            confidence_score=carried_confidence,
            bounding_boxes={},
        )
