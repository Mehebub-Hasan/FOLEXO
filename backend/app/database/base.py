"""Declarative base and shared column conventions.

Conventions enforced here (docs/DATABASE.md §1):
- UUID primary keys with a ``gen_random_uuid()`` server default,
- timezone-aware timestamps with ``now()`` server defaults,
- deterministic constraint naming so Alembic autogenerate stays stable.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """``created_at`` / ``updated_at`` for mutable tables.

    ``updated_at`` is maintained by the ORM (``onupdate``) rather than the
    docs' PL/pgSQL trigger: schema changes flow through Alembic and Phase 2
    keeps DDL trigger-free. Revisit if RLS-direct write paths are ever added.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
