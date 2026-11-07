"""AuditLog model — append-only, tenant-scoped audit trail (docs/DATABASE.md §6.1).

No UPDATE/DELETE path exists by design: audit rows are written once by the
API/worker and read forever. ``entity_id`` is polymorphic (no FK) so history
survives deletion of the referenced row.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Supabase auth.users id; null = system/worker actor.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID())
    action: Mapped[str] = mapped_column(Text(), nullable=False)
    entity_type: Mapped[str] = mapped_column(Text(), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID())
    # Named "metadata" in the database; the attribute avoids SQLAlchemy's
    # reserved ``.metadata`` on declarative models.
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB(), nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_audit_logs_org_created_at", "organization_id", "created_at"),
        Index(
            "ix_audit_logs_org_entity",
            "organization_id",
            "entity_type",
            "entity_id",
        ),
    )
