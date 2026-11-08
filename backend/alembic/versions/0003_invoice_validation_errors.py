"""Invoice validation errors (deterministic rules engine output).

Phase 5 step 1: invoices.validation_errors stores the structured
ValidationIssue list produced by app/modules/validation after extraction.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("validation_errors", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("invoices", "validation_errors")
