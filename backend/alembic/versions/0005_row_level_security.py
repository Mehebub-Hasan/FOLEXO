"""Row-Level Security policies + workspace-access helper (Phase 7 step 2).

Single source of truth: supabase/migrations/0002_row_level_security.sql.
This revision executes that file verbatim so `alembic upgrade head` remains
the only apply path. Policies are TO `authenticated` (Supabase direct-client
access only) — the API/worker connect as `postgres`/`service_role` and are
intentionally unaffected, otherwise every backend query would be filtered by
a helper that reads auth.uid() (NULL outside Supabase's request context).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-10
"""

from pathlib import Path
from typing import Sequence, Union

from alembic import context, op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SQL_FILE = (
    Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "0002_row_level_security.sql"
)

_POLICIES = [
    ("organizations", "organizations_select"),
    ("organization_members", "organization_members_select"),
    ("vendors", "vendors_select"),
    ("vendors", "vendors_insert"),
    ("vendors", "vendors_update"),
    ("vendors", "vendors_delete"),
    ("invoices", "invoices_select"),
    ("invoices", "invoices_insert"),
    ("invoices", "invoices_update"),
    ("invoices", "invoices_delete"),
    ("invoice_items", "invoice_items_select"),
    ("invoice_items", "invoice_items_insert"),
    ("invoice_items", "invoice_items_update"),
    ("invoice_items", "invoice_items_delete"),
    ("document_files", "document_files_select"),
    ("document_files", "document_files_insert"),
    ("document_files", "document_files_update"),
    ("document_files", "document_files_delete"),
    ("extraction_runs", "extraction_runs_select"),
    ("extraction_runs", "extraction_runs_insert"),
    ("extraction_runs", "extraction_runs_update"),
    ("extraction_runs", "extraction_runs_delete"),
    ("audit_logs", "audit_logs_select"),
    ("audit_logs", "audit_logs_insert"),
]


def _run_script(sql: str) -> None:
    # Online: raw multi-statement script (dollar-quoted function body) straight
    # to the driver's simple query protocol. Offline (--sql): verbatim emission
    # into the generated script.
    if context.is_offline_mode():
        op.execute(sql)
    else:
        op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run_script(SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    # PostgreSQL cannot DROP VALUE from an enum, and policy drops are safe to
    # reverse — RLS itself stays enabled (no data-path change, just denies
    # direct-client access).
    drop_statements = "\n".join(
        f"DROP POLICY IF EXISTS {policy} ON {table};"
        for table, policy in reversed(_POLICIES)
    ) + "\nDROP FUNCTION IF EXISTS auth.current_user_has_workspace_access(UUID);"
    _run_script(drop_statements)
