"""ARQ job handlers for the invoice extraction pipeline.

Phase 4 step 2: the real pipeline runs via the ExtractionOrchestrator
(Azure DI Stage A → confidence gate → conditional OpenAI Stage B, ADR-003).

Status contract (docs/ARCHITECTURE.md §5.3):
    UPLOADED → PROCESSING → NEEDS_REVIEW | FAILED

Observability: every attempt records an `extraction_runs` row (the pipeline
state of record) with started/finished timestamps, duration, and the error
message on failure. Redis is *not* durable state — the database rows are.

Failure policy: any orchestrator exception is terminal for this attempt —
the invoice is marked FAILED with the error recorded on the run row. Retry
classification (transient provider errors → re-raise for arq) lands with the
provider-hardening pass.
"""

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
from app.modules.extraction.orchestrator import ExtractionOrchestrator

logger = logging.getLogger(__name__)

PROCESS_INVOICE_JOB = "process_invoice_task"


async def _run_extraction(
    session_factory, invoice_uuid: uuid.UUID, run_id: uuid.UUID
) -> None:
    """Seam over the orchestrator so tests can stub the pipeline."""
    orchestrator = ExtractionOrchestrator(session_factory)
    await orchestrator.run(invoice_uuid, run_id)


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
        if invoice.status not in (InvoiceStatus.UPLOADED, InvoiceStatus.QUEUED):
            # arq retries can re-deliver after a terminal failure; never
            # resurrect a NEEDS_REVIEW/FAILED invoice from a stale job.
            # QUEUED = reprocess flow (Phase 5 step 2).
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
        # ── Pipeline stages (Azure Stage A → gate → Stage B) ──
        await _run_extraction(session_factory, invoice_uuid, run_id)

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

        elapsed = int((time.monotonic() - started_monotonic) * 1000)
        logger.info("invoice %s: needs_review (%d ms)", invoice_id, elapsed)
        return "needs_review"

    except Exception as exc:  # noqa: BLE001 — terminal handling; provider-hardening pass adds retry classification
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
