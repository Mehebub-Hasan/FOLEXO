"""Extraction orchestrator — Stage A → confidence gate → conditional Stage B →
persistence (ADR-003, docs/ARCHITECTURE.md §6).

Gate: the OpenAI fallback runs when overall confidence is below the
configured threshold OR any of line_items / vendor_name / total_amount is
missing. Otherwise Stage A's output is kept as-is.

Persistence contract:
- `extraction_runs` gets the audit trail (primary_provider="azure_di",
  raw_output → raw_payload, normalized_output, confidence_score, latency →
  duration_ms, fallback_used, final engine).
- `invoices` gets header fields, vendor link (matched or created by tax id /
  normalized name), engine attribution, confidence, and transitions to
  `needs_review`.
- `invoice_items` is replaced wholesale for machine rows (is_corrected =
  false) so reprocessing never clobbers human corrections (FR-9).

Callers (the ARQ task) own the FAILED transition: any exception raised here
bubbles up to `process_invoice_task`.
"""

import logging
import re
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models import (
    DocumentFile,
    ExtractionEngine,
    ExtractionRun,
    ExtractionRunStatus,
    Invoice,
    InvoiceItem,
    InvoiceStatus,
    Vendor,
)
from app.modules.extraction.azure_extractor import AzureInvoiceExtractor
from app.modules.extraction.base import BaseInvoiceExtractor
from app.modules.extraction.openai_extractor import OpenAIFallbackExtractor
from app.modules.extraction.schemas import (
    ExtractedInvoiceData,
    ExtractionError,
)

logger = logging.getLogger(__name__)

PRIMARY_PROVIDER = "azure_di"
_QUANT = Decimal("0.0001")
_UNSAFE_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


def _quantize(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return value.quantize(_QUANT)
    except (InvalidOperation, ValueError):
        return None


class DocumentFetcher(Protocol):
    """Supplies document bytes for a stored object."""

    async def fetch(self, storage_path: str) -> bytes: ...


class SupabaseDocumentFetcher:
    """Downloads the stored source document via the Storage REST API."""

    def __init__(self, base_url: str, service_role_key: str, bucket: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_role_key = service_role_key
        self._bucket = bucket

    async def fetch(self, storage_path: str) -> bytes:
        import httpx

        if not self._base_url or not self._service_role_key:
            raise ExtractionError("Supabase storage is not configured for document fetch")
        endpoint = f"{self._base_url}/storage/v1/object/{self._bucket}/{storage_path}"
        headers = {
            "Authorization": f"Bearer {self._service_role_key}",
            "apikey": self._service_role_key,
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(endpoint, headers=headers)
        except Exception as exc:
            raise ExtractionError(f"document fetch failed: {exc}") from exc
        if response.status_code >= 400:
            raise ExtractionError(
                f"document fetch rejected ({response.status_code}) for {storage_path}"
            )
        return response.content


class ExtractionOrchestrator:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        document_fetcher: DocumentFetcher | None = None,
        primary_extractor: BaseInvoiceExtractor | None = None,
        fallback_extractor: BaseInvoiceExtractor | None = None,
        confidence_threshold: float | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._fetcher = document_fetcher or SupabaseDocumentFetcher(
            base_url=settings.supabase_url or "",
            service_role_key=settings.supabase_service_role_key or "",
            bucket=settings.supabase_storage_bucket,
        )
        self._primary = primary_extractor or AzureInvoiceExtractor.from_settings()
        self._fallback = fallback_extractor or OpenAIFallbackExtractor.from_settings()
        self._threshold = (
            confidence_threshold
            if confidence_threshold is not None
            else settings.extraction_confidence_threshold
        )

    async def run(self, invoice_id: uuid.UUID, run_id: uuid.UUID) -> ExtractedInvoiceData:
        started_monotonic = time.monotonic()

        # ── Load context rows ──
        async with self._session_factory() as session:
            invoice = await session.get(Invoice, invoice_id)
            if invoice is None:
                raise ExtractionError(f"invoice {invoice_id} not found")
            document = await session.get(DocumentFile, invoice.document_id)
            if document is None:
                raise ExtractionError(f"document {invoice.document_id} missing for invoice {invoice_id}")
            uploaded_by_id = document.uploaded_by_id

        # ── Stage A + gate + conditional Stage B ──
        document_bytes = await self._fetcher.fetch(document.storage_path)
        primary_data = await self._primary.extract(document_bytes, document.mime_type)

        fallback_used, final_data = await self._apply_fallback(
            document_bytes, document.mime_type, primary_data
        )
        final_engine = (
            ExtractionEngine.OPENAI_FALLBACK if fallback_used else ExtractionEngine.AZURE_DI
        )

        latency_ms = int((time.monotonic() - started_monotonic) * 1000)
        await self._persist(
            invoice_id,
            run_id,
            final_data,
            primary_raw=getattr(self._primary, "last_raw_payload", None),
            fallback_used=fallback_used,
            final_engine=final_engine,
            latency_ms=latency_ms,
            uploaded_by_id=uploaded_by_id,
        )
        return final_data

    async def _apply_fallback(
        self,
        document_bytes: bytes,
        mime_type: str,
        primary_data: ExtractedInvoiceData,
    ) -> tuple[bool, ExtractedInvoiceData]:
        needs_fallback = (
            primary_data.confidence_score < self._threshold
            or not primary_data.line_items
            or primary_data.vendor_name is None
            or primary_data.total_amount is None
        )
        if not needs_fallback:
            return False, primary_data

        logger.info(
            "fallback triggered (confidence=%.2f, items=%d, vendor=%s, total=%s)",
            primary_data.confidence_score,
            len(primary_data.line_items),
            primary_data.vendor_name is not None,
            primary_data.total_amount is not None,
        )
        context = {
            "ocr_text": getattr(self._primary, "last_ocr_text", None),
            "azure_extraction": primary_data.model_dump(mode="json"),
        }
        final_data = await self._fallback.extract(document_bytes, mime_type, context=context)
        return True, final_data

    async def _persist(
        self,
        invoice_id: uuid.UUID,
        run_id: uuid.UUID,
        data: ExtractedInvoiceData,
        *,
        primary_raw: dict | None,
        fallback_used: bool,
        final_engine: ExtractionEngine,
        latency_ms: int,
        uploaded_by_id: uuid.UUID,
    ) -> None:
        finished_at = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            invoice = await session.get(Invoice, invoice_id)
            if invoice is None:
                raise ExtractionError(f"invoice {invoice_id} vanished mid-pipeline")

            vendor = await self._resolve_vendor(
                session, invoice, data, uploaded_by_id=uploaded_by_id
            )

            invoice.vendor_id = vendor.id if vendor else None
            invoice.invoice_number = data.invoice_number
            invoice.invoice_date = data.invoice_date
            invoice.due_date = data.due_date
            invoice.currency_code = self._safe_currency(data.currency)
            invoice.subtotal_amount = _quantize(data.subtotal)
            invoice.tax_amount = _quantize(data.tax_amount)
            invoice.total_amount = _quantize(data.total_amount)
            invoice.extraction_engine = final_engine
            invoice.extraction_finished_at = finished_at
            invoice.confidence_avg = self._safe_confidence(data.confidence_score)
            invoice.confidence_min = self._items_min_confidence(data) or invoice.confidence_avg

            # Replace machine rows; corrected rows survive reprocessing (FR-9).
            await session.execute(
                delete(InvoiceItem).where(
                    InvoiceItem.invoice_id == invoice_id,
                    InvoiceItem.is_corrected == False,  # noqa: E712 — SQL boolean
                )
            )
            for position, item in enumerate(data.line_items, start=1):
                session.add(
                    InvoiceItem(
                        organization_id=invoice.organization_id,
                        invoice_id=invoice.id,
                        line_number=position,
                        description=item.description,
                        quantity=_quantize(item.quantity),
                        unit_price=_quantize(item.unit_price),
                        tax_rate=None,
                        line_total=_quantize(item.line_total),
                        page_number=item.page_number,
                        confidence=self._safe_confidence(item.confidence),
                        bbox=(
                            {
                                "unit": "normalized",
                                "page_number": item.page_number,
                                "polygon": item.bounding_box,
                            }
                            if item.bounding_box
                            else None
                        ),
                        source_engine=final_engine,
                    )
                )

            run = await session.get(ExtractionRun, run_id)
            if run is not None:
                run.status = ExtractionRunStatus.SUCCEEDED
                run.engine = final_engine
                run.primary_provider = PRIMARY_PROVIDER
                run.raw_payload = primary_raw
                run.normalized_output = data.model_dump(mode="json")
                run.confidence_score = self._safe_confidence(data.confidence_score)
                run.fallback_used = fallback_used
                run.duration_ms = latency_ms
                run.finished_at = finished_at

            invoice.status = InvoiceStatus.NEEDS_REVIEW
            await session.commit()

        logger.info(
            "invoice %s extracted: engine=%s fallback=%s items=%d (%d ms)",
            invoice_id,
            final_engine.value,
            fallback_used,
            len(data.line_items),
            latency_ms,
        )

    async def _resolve_vendor(
        self,
        session: AsyncSession,
        invoice: Invoice,
        data: ExtractedInvoiceData,
        *,
        uploaded_by_id: uuid.UUID,
    ) -> Vendor | None:
        """Match by tax id first, then normalized name; create when unknown."""
        if not data.vendor_name and not data.vendor_tax_id:
            return None

        if data.vendor_tax_id:
            vendor = (
                await session.execute(
                    select(Vendor).where(
                        Vendor.organization_id == invoice.organization_id,
                        Vendor.tax_id == data.vendor_tax_id,
                    )
                )
            ).scalar_one_or_none()
            if vendor is not None:
                return vendor

        if data.vendor_name:
            name_norm = _UNSAFE_SLUG_CHARS.sub("-", data.vendor_name.strip().lower()).strip("-")
            vendor = (
                await session.execute(
                    select(Vendor).where(
                        Vendor.organization_id == invoice.organization_id,
                        Vendor.name_norm == name_norm,
                    )
                )
            ).scalar_one_or_none()
            if vendor is not None:
                return vendor

            vendor = Vendor(
                organization_id=invoice.organization_id,
                name=data.vendor_name,
                name_norm=name_norm,
                tax_id=data.vendor_tax_id,
                default_currency=data.currency,
                created_by_id=uploaded_by_id,
            )
            session.add(vendor)
            await session.flush()
            return vendor
        return None

    @staticmethod
    def _safe_currency(currency: str | None) -> str | None:
        if not currency:
            return None
        normalized = currency.strip().upper()
        return normalized if re.fullmatch(r"[A-Z]{3}", normalized) else None

    @staticmethod
    def _safe_confidence(value: float | None) -> Decimal | None:
        if value is None:
            return None
        clamped = max(0.0, min(1.0, value))
        return Decimal(str(clamped))

    @staticmethod
    def _items_min_confidence(data: ExtractedInvoiceData) -> Decimal | None:
        confidences = [item.confidence for item in data.line_items if item.confidence is not None]
        if not confidences:
            return None
        return Decimal(str(min(confidences)))
