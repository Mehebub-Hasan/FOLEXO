"""Initial FOLEXO schema.

Tables: organizations, organization_members, vendors, document_files,
invoices, invoice_items, extraction_runs, audit_logs.
Enums: organization_role, extraction_engine, extraction_run_status,
invoice_status.

Mirrors app/models/* (docs/DATABASE.md, vocabulary renamed: tenants →
organizations, suppliers → vendors, line items → invoice_items,
processing_jobs + extraction_artifacts merged into extraction_runs).

Enum types are created with duplicate-safe DO blocks and column types use
``create_type=False`` so this migration is idempotent-safe and does not depend
on SQLAlchemy's per-type creation bookkeeping across separate create_table
calls.

Revision ID: 0001
Revises:
Create Date: 2026-11-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ENUMS: dict[str, tuple[str, ...]] = {
    "organization_role": ("owner", "admin", "member", "viewer"),
    "extraction_engine": ("azure_di", "openai_fallback", "manual"),
    "extraction_run_status": ("queued", "running", "succeeded", "failed"),
    "invoice_status": (
        "uploaded",
        "processing",
        "needs_review",
        "ready",
        "approved",
        "rejected",
        "exported",
        "archived",
        "failed",
    ),
}


def _create_enums() -> None:
    for name, values in ENUMS.items():
        joined = ", ".join(f"'{value}'" for value in values)
        op.execute(
            f"DO $$ BEGIN CREATE TYPE {name} AS ENUM ({joined}); "
            "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
        )


def _drop_enums() -> None:
    for name in ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {name}")


def upgrade() -> None:
    _create_enums()

    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("default_currency", sa.String(length=3), server_default="USD", nullable=False),
        sa.Column("created_by_id", postgresql.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 120", name="ck_organizations_name_length"),
        sa.CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$'", name="ck_organizations_slug_format"),
        sa.CheckConstraint("country_code IS NULL OR country_code ~ '^[A-Z]{2}$'", name="ck_organizations_country_code_format"),
        sa.CheckConstraint("default_currency ~ '^[A-Z]{3}$'", name="ck_organizations_currency_format"),
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
        comment="Tenant root — every business record hangs off an organization.",
    )
    op.create_table(
        "organization_members",
        sa.Column("organization_id", postgresql.UUID(), nullable=False),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(name="organization_role", create_type=False),
            server_default="member",
            nullable=False,
        ),
        sa.Column("invited_by_id", postgresql.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_organization_members_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("organization_id", "user_id", name="pk_organization_members"),
        comment=(
            "Membership + role per user. Invariants enforced in the API layer: "
            "founding member is owner, an organization cannot lose its last owner."
        ),
    )
    op.create_table(
        "vendors",
        sa.Column("id", postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_norm", sa.Text(), nullable=False),
        sa.Column("tax_id", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("address_line1", sa.Text(), nullable=True),
        sa.Column("address_line2", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("postal_code", sa.Text(), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("default_currency", sa.String(length=3), nullable=True),
        sa.Column("payment_terms_days", sa.SmallInteger(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_vendors_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 200", name="ck_vendors_name_length"),
        sa.CheckConstraint(
            "payment_terms_days IS NULL OR payment_terms_days BETWEEN 0 AND 365",
            name="ck_vendors_payment_terms_range",
        ),
        sa.CheckConstraint("country_code IS NULL OR country_code ~ '^[A-Z]{2}$'", name="ck_vendors_country_code_format"),
        sa.CheckConstraint("default_currency IS NULL OR default_currency ~ '^[A-Z]{3}$'", name="ck_vendors_currency_format"),
        sa.PrimaryKeyConstraint("id", name="pk_vendors"),
        sa.UniqueConstraint("organization_id", "name_norm", name="uq_vendors_org_name_norm"),
    )
    op.create_index(
        "uq_vendors_org_tax_id",
        "vendors",
        ["organization_id", "tax_id"],
        unique=True,
        postgresql_where=sa.text("tax_id IS NOT NULL"),
    )
    op.create_table(
        "document_files",
        sa.Column("id", postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("uploaded_by_id", postgresql.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_document_files_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("file_size_bytes > 0", name="ck_document_files_size_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_document_files"),
        comment=(
            "Metadata for uploaded source documents; bytes live in Supabase "
            "Storage (private invoices bucket)."
        ),
    )
    op.create_index("ix_document_files_organization_id", "document_files", ["organization_id"])
    op.create_index(
        "ix_document_files_org_content_hash",
        "document_files",
        ["organization_id", "content_hash"],
    )
    op.create_table(
        "invoices",
        sa.Column("id", postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(), nullable=False),
        sa.Column("document_id", postgresql.UUID(), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(), nullable=True),
        sa.Column("invoice_number", sa.Text(), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("currency_code", sa.String(length=3), nullable=True),
        sa.Column("po_number", sa.Text(), nullable=True),
        sa.Column("subtotal_amount", sa.Numeric(precision=15, scale=4), nullable=True),
        sa.Column("tax_amount", sa.Numeric(precision=15, scale=4), nullable=True),
        sa.Column("total_amount", sa.Numeric(precision=15, scale=4), nullable=True),
        sa.Column(
            "extraction_engine",
            postgresql.ENUM(name="extraction_engine", create_type=False),
            nullable=True,
        ),
        sa.Column("confidence_min", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("confidence_avg", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("extraction_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extraction_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_possible_duplicate", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="invoice_status", create_type=False),
            server_default="uploaded",
            nullable=False,
        ),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_invoices_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["document_files.id"],
            name="fk_invoices_document_id_document_files",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["vendor_id"],
            ["vendors.id"],
            name="fk_invoices_vendor_id_vendors",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "subtotal_amount IS NULL OR subtotal_amount >= 0",
            name="ck_invoices_subtotal_non_negative",
        ),
        sa.CheckConstraint(
            "tax_amount IS NULL OR tax_amount >= 0",
            name="ck_invoices_tax_non_negative",
        ),
        sa.CheckConstraint(
            "total_amount IS NULL OR total_amount >= 0",
            name="ck_invoices_total_non_negative",
        ),
        sa.CheckConstraint(
            "due_date IS NULL OR invoice_date IS NULL OR due_date >= invoice_date",
            name="ck_invoices_due_after_invoice_date",
        ),
        sa.CheckConstraint(
            "confidence_min IS NULL OR confidence_min BETWEEN 0 AND 1",
            name="ck_invoices_confidence_min_range",
        ),
        sa.CheckConstraint(
            "confidence_avg IS NULL OR confidence_avg BETWEEN 0 AND 1",
            name="ck_invoices_confidence_avg_range",
        ),
        sa.CheckConstraint(
            "currency_code IS NULL OR currency_code ~ '^[A-Z]{3}$'",
            name="ck_invoices_currency_format",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invoices"),
    )
    op.create_index("ix_invoices_org_created_at", "invoices", ["organization_id", "created_at"])
    op.create_index("ix_invoices_org_status", "invoices", ["organization_id", "status"])
    op.create_index("ix_invoices_org_vendor", "invoices", ["organization_id", "vendor_id"])
    op.create_index(
        "ix_invoices_org_invoice_number",
        "invoices",
        ["organization_id", "invoice_number"],
        postgresql_where=sa.text("invoice_number IS NOT NULL"),
    )
    op.create_table(
        "invoice_items",
        sa.Column("id", postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("unit_price", sa.Numeric(precision=15, scale=4), nullable=True),
        sa.Column("tax_rate", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("line_total", sa.Numeric(precision=15, scale=4), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column(
            "source_engine",
            postgresql.ENUM(name="extraction_engine", create_type=False),
            nullable=True,
        ),
        sa.Column("is_corrected", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_invoice_items_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name="fk_invoice_items_invoice_id_invoices",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("line_number BETWEEN 1 AND 10000", name="ck_invoice_items_line_number_range"),
        sa.CheckConstraint(
            "quantity IS NULL OR quantity >= 0",
            name="ck_invoice_items_quantity_non_negative",
        ),
        sa.CheckConstraint(
            "tax_rate IS NULL OR tax_rate BETWEEN 0 AND 1",
            name="ck_invoice_items_tax_rate_range",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name="ck_invoice_items_confidence_range",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_items"),
        sa.UniqueConstraint("invoice_id", "line_number", name="uq_invoice_items_invoice_line_number"),
    )
    op.create_index("ix_invoice_items_invoice", "invoice_items", ["invoice_id", "line_number"])
    op.create_table(
        "extraction_runs",
        sa.Column("id", postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(), nullable=False),
        sa.Column(
            "engine",
            postgresql.ENUM(name="extraction_engine", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="extraction_run_status", create_type=False),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_extraction_runs_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name="fk_extraction_runs_invoice_id_invoices",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("attempt >= 0 AND max_attempts >= 1", name="ck_extraction_runs_attempts"),
        sa.PrimaryKeyConstraint("id", name="pk_extraction_runs"),
        comment=(
            "One extraction attempt against an invoice (pipeline state + verbatim "
            "provider payload for offline confidence-gate tuning)."
        ),
    )
    op.create_index("ix_extraction_runs_organization_id", "extraction_runs", ["organization_id"])
    op.create_index("ix_extraction_runs_invoice", "extraction_runs", ["invoice_id", "queued_at"])
    op.create_index(
        "ix_extraction_runs_active",
        "extraction_runs",
        ["status", "queued_at"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(), nullable=False),
        sa.Column("actor_id", postgresql.UUID(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", postgresql.UUID(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_audit_logs_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
        comment="Append-only; no UPDATE/DELETE path exists by design.",
    )
    op.create_index("ix_audit_logs_org_created_at", "audit_logs", ["organization_id", "created_at"])
    op.create_index(
        "ix_audit_logs_org_entity",
        "audit_logs",
        ["organization_id", "entity_type", "entity_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_org_entity", table_name="audit_logs")
    op.drop_index("ix_audit_logs_org_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_extraction_runs_active", table_name="extraction_runs")
    op.drop_index("ix_extraction_runs_invoice", table_name="extraction_runs")
    op.drop_index("ix_extraction_runs_organization_id", table_name="extraction_runs")
    op.drop_table("extraction_runs")
    op.drop_index("ix_invoice_items_invoice", table_name="invoice_items")
    op.drop_table("invoice_items")
    op.drop_index("ix_invoices_org_invoice_number", table_name="invoices")
    op.drop_index("ix_invoices_org_vendor", table_name="invoices")
    op.drop_index("ix_invoices_org_status", table_name="invoices")
    op.drop_index("ix_invoices_org_created_at", table_name="invoices")
    op.drop_table("invoices")
    op.drop_index("ix_document_files_org_content_hash", table_name="document_files")
    op.drop_index("ix_document_files_organization_id", table_name="document_files")
    op.drop_table("document_files")
    op.drop_index("uq_vendors_org_tax_id", table_name="vendors")
    op.drop_table("vendors")
    op.drop_table("organization_members")
    op.drop_table("organizations")
    _drop_enums()
