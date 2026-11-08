"""Invoice and InvoiceItem models (docs/DATABASE.md §5.1–§5.2, renamed).

Money columns are Numeric(15, 4) — never floats (ADR-007). Header columns are
nullable because they populate asynchronously during extraction.
"""

import decimal
import enum
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin
from app.models.extraction import ExtractionEngine, extraction_engine_enum
from app.models.organization import _enum_values


class InvoiceStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPORTED = "exported"
    ARCHIVED = "archived"
    FAILED = "failed"


invoice_status_enum = SAEnum(
    InvoiceStatus, name="invoice_status", values_callable=_enum_values
)


class Invoice(Base, TimestampMixin):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Uploaded source document; RESTRICT keeps documents from being orphaned —
    # the GDPR delete flow removes the invoice row first, then the document.
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("document_files.id", ondelete="RESTRICT"),
        nullable=False,
    )
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(), ForeignKey("vendors.id", ondelete="SET NULL")
    )

    # ── Authoritative header fields (post-extraction / post-review) ──
    invoice_number: Mapped[str | None] = mapped_column(Text())
    invoice_date: Mapped[date | None] = mapped_column(Date())
    due_date: Mapped[date | None] = mapped_column(Date())
    currency_code: Mapped[str | None] = mapped_column(String(3))
    po_number: Mapped[str | None] = mapped_column(Text())
    subtotal_amount: Mapped[decimal.Decimal | None] = mapped_column(Numeric(15, 4))
    tax_amount: Mapped[decimal.Decimal | None] = mapped_column(Numeric(15, 4))
    total_amount: Mapped[decimal.Decimal | None] = mapped_column(Numeric(15, 4))

    # ── Extraction metadata ──────────────────────────────────────────
    extraction_engine: Mapped[ExtractionEngine | None] = mapped_column(
        extraction_engine_enum
    )
    confidence_min: Mapped[decimal.Decimal | None] = mapped_column(Numeric(4, 3))
    confidence_avg: Mapped[decimal.Decimal | None] = mapped_column(Numeric(4, 3))
    extraction_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    extraction_finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    is_possible_duplicate: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=False, server_default=false()
    )

    # ── Lifecycle (docs/ARCHITECTURE.md §5.3) ────────────────────────
    status: Mapped[InvoiceStatus] = mapped_column(
        invoice_status_enum,
        nullable=False,
        default=InvoiceStatus.UPLOADED,
        server_default="uploaded",
    )
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text())

    __table_args__ = (
        CheckConstraint(
            "subtotal_amount IS NULL OR subtotal_amount >= 0",
            name="ck_invoices_subtotal_non_negative",
        ),
        CheckConstraint(
            "tax_amount IS NULL OR tax_amount >= 0",
            name="ck_invoices_tax_non_negative",
        ),
        CheckConstraint(
            "total_amount IS NULL OR total_amount >= 0",
            name="ck_invoices_total_non_negative",
        ),
        CheckConstraint(
            "due_date IS NULL OR invoice_date IS NULL OR due_date >= invoice_date",
            name="ck_invoices_due_after_invoice_date",
        ),
        CheckConstraint(
            "confidence_min IS NULL OR confidence_min BETWEEN 0 AND 1",
            name="ck_invoices_confidence_min_range",
        ),
        CheckConstraint(
            "confidence_avg IS NULL OR confidence_avg BETWEEN 0 AND 1",
            name="ck_invoices_confidence_avg_range",
        ),
        CheckConstraint(
            "currency_code IS NULL OR currency_code ~ '^[A-Z]{3}$'",
            name="ck_invoices_currency_format",
        ),
        Index("ix_invoices_org_created_at", "organization_id", "created_at"),
        Index("ix_invoices_org_status", "organization_id", "status"),
        Index("ix_invoices_org_vendor", "organization_id", "vendor_id"),
        Index(
            "ix_invoices_org_invoice_number",
            "organization_id",
            "invoice_number",
            postgresql_where=text("invoice_number IS NOT NULL"),
        ),
    )


class InvoiceItem(Base, TimestampMixin):
    __tablename__ = "invoice_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    line_number: Mapped[int] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(Text())
    quantity: Mapped[decimal.Decimal | None] = mapped_column(Numeric(12, 4))
    unit_price: Mapped[decimal.Decimal | None] = mapped_column(Numeric(15, 4))
    # Fraction, e.g. 0.2000 = 20%
    tax_rate: Mapped[decimal.Decimal | None] = mapped_column(Numeric(6, 4))
    line_total: Mapped[decimal.Decimal | None] = mapped_column(Numeric(15, 4))
    page_number: Mapped[int | None] = mapped_column()
    confidence: Mapped[decimal.Decimal | None] = mapped_column(Numeric(4, 3))
    # Normalized polygon, ARCHITECTURE.md §7 shape:
    # {"unit": "normalized", "page_number": n, "polygon": [[x, y], ...]}
    bbox: Mapped[dict[str, Any] | None] = mapped_column(JSONB())
    source_engine: Mapped[ExtractionEngine | None] = mapped_column(
        extraction_engine_enum
    )
    is_corrected: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=False, server_default=false()
    )

    __table_args__ = (
        CheckConstraint(
            "line_number BETWEEN 1 AND 10000", name="ck_invoice_items_line_number_range"
        ),
        CheckConstraint(
            "quantity IS NULL OR quantity >= 0",
            name="ck_invoice_items_quantity_non_negative",
        ),
        CheckConstraint(
            "tax_rate IS NULL OR tax_rate BETWEEN 0 AND 1",
            name="ck_invoice_items_tax_rate_range",
        ),
        CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name="ck_invoice_items_confidence_range",
        ),
        UniqueConstraint(
            "invoice_id", "line_number", name="uq_invoice_items_invoice_line_number"
        ),
        Index("ix_invoice_items_invoice", "invoice_id", "line_number"),
    )
