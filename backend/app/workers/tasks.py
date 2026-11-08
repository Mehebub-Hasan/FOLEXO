"""ARQ job handlers for the invoice extraction pipeline.

Phase 4 step 1 ships the lifecycle + state machine only; Phase 4 step 2
replaces `run_mock_extraction` with the real dual-stage pipeline
(Azure DI Stage A → confidence gate → conditional OpenAI Stage B, ADR-003).

Status contract (docs/ARCHITECTURE.md §5.3):
    UPLOADED → PROCESSING → NEEDS_REVIEW | FAILED

Observability: every attempt records an `extraction_runs` row (the pipeline
state of record) with started/finished timestamps, duration, and the error
message on failure. Redis is *not* durable state — the database rows are.
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone

from app.models import (
    ExtractionRun,
    ExtractionRunStatus,
    Invoice,
    InvoiceStatus,
)

logger = logging.getLogger(__name__)

PROCESS_INVOICE_JOB = "process_invoice_task"

# Phase 4.2 removes this placeholder along with the whole mock.
MOCK_STAGE_DELAY_SECONDS = 2.0


async def run_mock_extraction() -> None:
    """Placeholder for the real OCR/LLM stages (Phase 4 step 2)."""
    await asyncio.sleep(MOCK_STAGE_DELAY_SECONDS)


async def process_invoice_task(ctx: dict, invoice_id: str) -> str:
    """Drive one invoice through the pipeline state machine.

    Returns a terminal marker for observability: "needs_review", "failed",
    "not_found", or "skipped" (invoice was no longer in UPLOADED — e.g. an
    arq retry after a terminal failure).
    """
    session_factory = ctx["session_factory"]
    invoice_uuid = uuid.UUID(invoice_id)
    started_monotonic = time.monotonic()

    async with session_factory() as session:
        invoice = await session.get(Invoice, invoice_uuid)
        if invoice is None:
            logger.warning("process_invoice_task: invoice %s not found — skipping", invoice_id)
            return "not_found"
        if invoice.status != InvoiceStatus.UPLOADED:
            # arq retries can re-deliver after a terminal failure; never
            # resurrect a FAILED/NEEDS_REVIEW invoice from a stale job.
            logger.info(
                "process_invoice_task: invoice %s in status %s — skipping",
                invoice_id,
                invoice.status.value,
            )
            return "skipped"

        run = ExtractionRun(
            organization_id=invoice.organization_id,
            invoice_id=invoice.id,
            status=ExtractionRunStatus.RUNNING,
            attempt=1,
            started_at=datetime.now(timezone.utc),
        )
        session.add(run)

        invoice.status = InvoiceStatus.PROCESSING
        invoice.extraction_started_at = run.started_at
        await session.commit()
        run_id = run.id

    logger.info("invoice %s: processing started (run %s)", invoice_id, run_id)

    try:
        # ── Pipeline stages (mock until Phase 4 step 2) ──
        await run_mock_extraction()

        finished_at = datetime.now(timezone.utc)
        async with session_factory() as session:
            done_invoice = await session.get(Invoice, invoice_uuid)
            if done_invoice is not None:
                done_invoice.status = InvoiceStatus.NEEDS_REVIEW
                done_invoice.extraction_finished_at = finished_at
            done_run = await session.get(ExtractionRun, run_id)
            if done_run is not None:
                done_run.status = ExtractionRunStatus.SUCCEEDED
                done_run.finished_at = finished_at
                done_run.duration_ms = int((time.monotonic() - started_monotonic) * 1000)
            await session.commit()

        logger.info("invoice %s: needs_review (%d ms)", invoice_id, int((time.monotonic() - started_monotonic) * 1000))
        return "needs_review"

    except Exception as exc:  # noqa: BLE001 — terminal handling; Phase 4.2 adds retry classification
        logger.exception("invoice %s: processing failed", invoice_id)
        failed_at = datetime.now(timezone.utc)
        async with session_factory() as session:
            failed_invoice = await session.get(Invoice, invoice_uuid)
            if failed_invoice is not None:
                failed_invoice.status = InvoiceStatus.FAILED
                failed_invoice.extraction_finished_at = failed_at
            failed_run = await session.get(ExtractionRun, run_id)
            if failed_run is not None:
                failed_run.status = ExtractionRunStatus.FAILED
                failed_run.finished_at = failed_at
                failed_run.duration_ms = int((time.monotonic() - started_monotonic) * 1000)
                failed_run.error_code = "EXTRACTION_FAILED"
                failed_run.error_message = str(exc)[:500]
            await session.commit()
        return "failed"
