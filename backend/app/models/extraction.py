"""ExtractionRun model — one pipeline attempt against an invoice.

Merges docs/DATABASE.md §5.4 + §5.5 (processing_jobs + extraction_artifacts):
pipeline state (status, attempts, timings, errors) and the verbatim provider
payload (raw JSONB for offline confidence-gate tuning, ADR-003) live on the
same row. References invoices by table-name string to avoid a circular import
(invoice.py imports this module's enums).

Phase 4 added audit columns for the dual-stage pipeline: primary_provider,
normalized_output, confidence_score, fallback_used.
"""

import decimal
import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.organization import _enum_values


class ExtractionEngine(str, enum.Enum):
    AZURE_DI = "azure_di"
    OPENAI_FALLBACK = "openai_fallback"
    MANUAL = "manual"


class ExtractionRunStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


extraction_engine_enum = SAEnum(
    ExtractionEngine, name="extraction_engine", values_callable=_enum_values
)

extraction_run_status_enum = SAEnum(
    ExtractionRunStatus, name="extraction_run_status", values_callable=_enum_values
)


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Engine that produced this run's output; null while queued/running.
    engine: Mapped[ExtractionEngine | None] = mapped_column(extraction_engine_enum)
    status: Mapped[ExtractionRunStatus] = mapped_column(
        extraction_run_status_enum,
        nullable=False,
        default=ExtractionRunStatus.QUEUED,
        server_default="queued",
    )
    attempt: Mapped[int] = mapped_column(Integer(), nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(
        Integer(), nullable=False, server_default="3"
    )
    error_code: Mapped[str | None] = mapped_column(Text())
    error_message: Mapped[str | None] = mapped_column(Text())
    http_status: Mapped[int | None] = mapped_column(Integer())
    # Verbatim provider response (Azure DI), including page geometry needed to
    # re-normalize bounding boxes without re-calling the provider.
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB())
    # Normalized (post-fallback-merge) extraction as JSON — the schema mirrors
    # ExtractedInvoiceData in app/modules/extraction/schemas.py.
    normalized_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB())
    confidence_score: Mapped[decimal.Decimal | None] = mapped_column(Numeric(4, 3))
    fallback_used: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=False, server_default=false()
    )
    # Provider that produced the primary (Stage A) extraction; constant
    # "azure_di" while ADR-003 holds.
    primary_provider: Mapped[str | None] = mapped_column(Text())
    duration_ms: Mapped[int | None] = mapped_column(Integer())
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "attempt >= 0 AND max_attempts >= 1", name="ck_extraction_runs_attempts"
        ),
        CheckConstraint(
            "confidence_score IS NULL OR confidence_score BETWEEN 0 AND 1",
            name="ck_extraction_runs_confidence_range",
        ),
        Index("ix_extraction_runs_invoice", "invoice_id", "queued_at"),
        Index(
            "ix_extraction_runs_active",
            "status",
            "queued_at",
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )
