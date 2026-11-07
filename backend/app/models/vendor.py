"""Vendor model — the supplier directory (docs/DATABASE.md §4.4, renamed)."""

import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class Vendor(Base, TimestampMixin):
    __tablename__ = "vendors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text(), nullable=False)
    # lower(trim(name)); app-maintained (the docs' §7.3 trigger becomes app
    # logic while schema changes flow through Alembic).
    name_norm: Mapped[str] = mapped_column(Text(), nullable=False)
    tax_id: Mapped[str | None] = mapped_column(Text())
    email: Mapped[str | None] = mapped_column(Text())
    phone: Mapped[str | None] = mapped_column(Text())
    address_line1: Mapped[str | None] = mapped_column(Text())
    address_line2: Mapped[str | None] = mapped_column(Text())
    city: Mapped[str | None] = mapped_column(Text())
    postal_code: Mapped[str | None] = mapped_column(Text())
    country_code: Mapped[str | None] = mapped_column(String(2))
    default_currency: Mapped[str | None] = mapped_column(String(3))
    payment_terms_days: Mapped[int | None] = mapped_column(SmallInteger())
    notes: Mapped[str | None] = mapped_column(Text())
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID())

    __table_args__ = (
        UniqueConstraint("organization_id", "name_norm", name="uq_vendors_org_name_norm"),
        Index(
            "uq_vendors_org_tax_id",
            "organization_id",
            "tax_id",
            unique=True,
            postgresql_where=text("tax_id IS NOT NULL"),
        ),
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 200", name="ck_vendors_name_length"
        ),
        CheckConstraint(
            "payment_terms_days IS NULL OR payment_terms_days BETWEEN 0 AND 365",
            name="ck_vendors_payment_terms_range",
        ),
        CheckConstraint(
            "country_code IS NULL OR country_code ~ '^[A-Z]{2}$'",
            name="ck_vendors_country_code_format",
        ),
        CheckConstraint(
            "default_currency IS NULL OR default_currency ~ '^[A-Z]{3}$'",
            name="ck_vendors_currency_format",
        ),
    )
