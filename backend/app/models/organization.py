"""Organization (tenant) and membership models.

Product vocabulary settled on "organization"; these carry the
docs/DATABASE.md §4.1–§4.2 tenant tables (tenants → organizations).

User references (``created_by_id``, ``user_id``) hold Supabase ``auth.users``
ids. No SQL-level FK is possible across schemas (and auth.users is managed by
Supabase), so membership integrity is enforced at the API layer — Phase 3.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


def _enum_values(e: type[enum.Enum]) -> list[str]:
    """Persist enum *values* (lowercase), not member names."""
    return [member.value for member in e]


class OrganizationRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text(), nullable=False)
    slug: Mapped[str] = mapped_column(Text(), nullable=False, unique=True)
    country_code: Mapped[str | None] = mapped_column(String(2))
    default_currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default="USD"
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID())

    __table_args__ = (
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 120", name="ck_organizations_name_length"
        ),
        CheckConstraint(
            "slug ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$'",
            name="ck_organizations_slug_format",
        ),
        CheckConstraint(
            "country_code IS NULL OR country_code ~ '^[A-Z]{2}$'",
            name="ck_organizations_country_code_format",
        ),
        CheckConstraint(
            "default_currency ~ '^[A-Z]{3}$'", name="ck_organizations_currency_format"
        ),
        {"comment": "Tenant root — every business record hangs off an organization."},
    )


class OrganizationMember(Base):
    __tablename__ = "organization_members"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(), primary_key=True)
    role: Mapped[OrganizationRole] = mapped_column(
        SAEnum(
            OrganizationRole,
            name="organization_role",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=OrganizationRole.MEMBER,
        server_default="member",
    )
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        {
            "comment": (
                "Membership + role per user. Invariants enforced in the API layer: "
                "founding member is owner, an organization cannot lose its last owner."
            )
        },
    )
