"""Queued invoice status + approval tracking columns.

Phase 5 step 2: reprocess transitions invoices through `queued` (new enum
value), and approval records who approved and when (approved_by_id is a
Supabase auth.users id — no FK across schemas, consistent with the rest).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enum value addition is idempotent; PG 12+ allows it inside a transaction
    # as long as the value is not used until commit.
    op.execute("ALTER TYPE invoice_status ADD VALUE IF NOT EXISTS 'queued'")
    op.add_column("invoices", sa.Column("approved_by_id", postgresql.UUID(), nullable=True))
    op.add_column(
        "invoices",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # Enum values cannot be removed in PostgreSQL; downgrade leaves 'queued'
    # in place (harmless, unused).
    op.drop_column("invoices", "approved_at")
    op.drop_column("invoices", "approved_by_id")
