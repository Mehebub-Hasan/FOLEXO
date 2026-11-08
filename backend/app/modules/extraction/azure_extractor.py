"""Azure Document Intelligence extractor — Stage A (ADR-003).

Uses the `prebuilt-invoice` model. The SDK is synchronous; the analyze call
runs in a worker thread to keep the ARQ event loop responsive.

Field mapping is written defensively (getattr chains, narrow conversions):
provider payload quirks degrade a single field to None instead of failing the
whole extraction. Raw provider response + OCR markdown are exposed as
instance attributes for the run audit and the Stage B context.
"""

import asyncio
import logging
from datetime import date as date_type
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.config import settings
from app.modules.extraction.base import BaseInvoiceExtractor
from app.modules.extraction.schemas import (
    ExtractedInvoiceData,
    ExtractedLineItem,
    ExtractionError,
    ExtractorConfigurationError,
)

logger = logging.getLogger(__name__)

_CORE_FIELD_KEYS = ("VendorName", "InvoiceId", "InvoiceDate", "DueDate", "InvoiceTotal")


def _attr(obj: Any, name: str) -> Any:
    return getattr(obj, name, None) if obj is not None else None


def _text(field: Any) -> str | None:
    value = _attr(field, "value_string")
    if value:
        return str(value).strip() or None
    content = _attr(field, "content")
    return str(content).strip() or None if content else None


def _date_value(field: Any) -> date_type | None:
    value = _attr(field, "value_date")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date_type):
        return value
    return None


def _amount(field: Any) -> Decimal | None:
    currency = _attr(field, "value_currency")
    amount = _attr(currency, "amount")
    if amount is None:
        amount = _attr(field, "value_number")
    if amount is None:
        amount = _attr(field, "value_integer")
    if amount is None:
        return None
    return Decimal(str(amount))


def _confidence(field: Any) -> float | None:
    value = _attr(field, "confidence")
    if value is None:
        return None
    return max(0.0, min(1.0, float(value)))


def _normalized_bbox(field: Any, page_sizes: dict[int, tuple[float, float]]) -> dict[str, Any] | None:
    regions = _attr(field, "bounding_regions") or []
    if not regions:
        return None
    region = regions[0]
    polygon = list(_attr(region, "polygon") or [])
    page_number = int(_attr(region, "page_number") or 1)
    width, height = page_sizes.get(page_number, (1.0, 1.0))
    if width <= 0 or height <= 0 or len(polygon) < 6:
        return None
    points = [
        [round(polygon[i] / width, 4), round(polygon[i + 1] / height, 4)]
        for i in range(0, len(polygon) - 1, 2)
    ]
    return {"unit": "normalized", "page_number": page_number, "polygon": points}


class AzureInvoiceExtractor(BaseInvoiceExtractor):
    def __init__(self, endpoint: str, api_key: str, model: str | None = None) -> None:
        if not endpoint or not api_key:
            raise ExtractorConfigurationError(
                "Azure Document Intelligence is not configured "
                "(AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT / _API_KEY)"
            )
        # Imported here so environments without the package can still import
        # the module graph for non-Azure paths (tests stub this class out).
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential

        self._client = DocumentIntelligenceClient(
            endpoint=endpoint, credential=AzureKeyCredential(api_key)
        )
        self._model = model or settings.azure_document_intelligence_model
        self.last_raw_payload: dict[str, Any] | None = None
        self.last_ocr_text: str | None = None

    @classmethod
    def from_settings(cls) -> "AzureInvoiceExtractor":
        return cls(
            endpoint=settings.azure_document_intelligence_endpoint or "",
            api_key=settings.azure_document_intelligence_api_key or "",
            model=settings.azure_document_intelligence_model,
        )

    async def extract(
        self,
        document_bytes: bytes,
        mime_type: str,
        context: dict[str, Any] | None = None,
    ) -> ExtractedInvoiceData:
        try:
            result = await asyncio.to_thread(self._analyze, document_bytes)
        except ExtractionError:
            raise
        except Exception as exc:  # azure.core.exceptions wrap many failure modes
            raise ExtractionError(f"Azure Document Intelligence failed: {exc}") from exc

        self.last_raw_payload = result.as_dict() if hasattr(result, "as_dict") else None
        self.last_ocr_text = getattr(result, "content", None)
        return self._to_schema(result)

    def _analyze(self, document_bytes: bytes) -> Any:
        from azure.ai.documentintelligence.models import AnalyzeDocumentRequest

        poller = self._client.begin_analyze_document(
            self._model, AnalyzeDocumentRequest(bytes_source=document_bytes)
        )
        return poller.result()

    def _to_schema(self, result: Any) -> ExtractedInvoiceData:
        documents = _attr(result, "documents") or []
        document = documents[0] if documents else None
        fields = _attr(document, "fields") or {}
        page_sizes: dict[int, tuple[float, float]] = {}
        for page in _attr(result, "pages") or []:
            number = int(_attr(page, "page_number") or 0)
            page_sizes[number] = (float(_attr(page, "width") or 1.0), float(_attr(page, "height") or 1.0))

        currency_code = _text(fields.get("CurrencyCode"))
        if not currency_code:
            total_currency = _attr(fields.get("InvoiceTotal"), "value_currency")
            currency_code = (_attr(total_currency, "code") or _attr(total_currency, "symbol") or "USD")
        currency_code = str(currency_code).upper()[:3] or "USD"

        overall_confidence = _confidence(document) or 0.0

        return ExtractedInvoiceData(
            vendor_name=_text(fields.get("VendorName")),
            vendor_tax_id=_text(fields.get("VendorTaxId")),
            invoice_number=_text(fields.get("InvoiceId")),
            invoice_date=_date_value(fields.get("InvoiceDate")),
            due_date=_date_value(fields.get("DueDate")),
            currency=currency_code,
            subtotal=_amount(fields.get("SubTotal")),
            tax_amount=_amount(fields.get("TotalTaxAmount")),
            total_amount=_amount(fields.get("InvoiceTotal")),
            line_items=self._line_items(fields.get("Items"), page_sizes),
            confidence_score=overall_confidence,
            bounding_boxes={
                key: bbox
                for key in _CORE_FIELD_KEYS
                if (bbox := _normalized_bbox(fields.get(key), page_sizes)) is not None
            },
        )

    def _line_items(
        self, items_field: Any, page_sizes: dict[int, tuple[float, float]]
    ) -> list[ExtractedLineItem]:
        entries = _attr(items_field, "value_array") or []
        items: list[ExtractedLineItem] = []
        for entry in entries:
            obj = _attr(entry, "value_object") or {}
            regions = _attr(entry, "bounding_regions") or []
            page_number = int(_attr(regions[0], "page_number") or 1) if regions else None
            polygon = _normalized_bbox(entry, page_sizes)
            items.append(
                ExtractedLineItem(
                    description=_text(obj.get("Description")),
                    quantity=_amount(obj.get("Quantity")),
                    unit_price=_amount(obj.get("UnitPrice")),
                    line_total=_amount(obj.get("Amount")),
                    sku=_text(obj.get("ProductCode")),
                    page_number=page_number,
                    confidence=_confidence(entry),
                    bounding_box=(polygon or {}).get("polygon"),
                )
            )
        return items
