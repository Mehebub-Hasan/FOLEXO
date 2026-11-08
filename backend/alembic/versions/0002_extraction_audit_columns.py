"""Extraction pipeline audit columns + line-item bounding boxes.

Phase 4 step 2: adds the dual-stage audit fields to extraction_runs
(primary_provider, normalized_output, confidence_score, fallback_used —
raw_output already exists as raw_payload, latency as duration_ms) and the
normalized bbox column to invoice_items so parsed line items keep their
document location (docs/ARCHITECTURE.md §7).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "extraction_runs",
        sa.Column("normalized_output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "extraction_runs",
        sa.Column("confidence_score", sa.Numeric(precision=4, scale=3), nullable=True),
    )
    op.add_column(
        "extraction_runs",
        sa.Column("fallback_used", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "extraction_runs",
        sa.Column("primary_provider", sa.Text(), nullable=True),
    )
    op.add_column(
        "invoice_items",
        sa.Column("bbox", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        "ck_extraction_runs_confidence_range",
        "extraction_runs",
        "confidence_score IS NULL OR confidence_score BETWEEN 0 AND 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_extraction_runs_confidence_range", "extraction_runs", type_="check"
    )
    op.drop_column("invoice_items", "bbox")
    op.drop_column("extraction_runs", "primary_provider")
    op.drop_column("extraction_runs", "fallback_used")
    op.drop_column("extraction_runs", "confidence_score")
    op.drop_column("extraction_runs", "normalized_output")
