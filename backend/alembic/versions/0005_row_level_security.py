"""Row-Level Security policies + workspace-access helper (Phase 7 step 2).

Single source of truth: supabase/migrations/0002_row_level_security.sql.
This revision replays that file statement by statement so `alembic upgrade
head` remains the only apply path. Policies are TO `authenticated` (Supabase
direct-client access only) — the API/worker connect as `postgres`/`service_role`
and are intentionally unaffected, otherwise every backend query would be
filtered by a helper that reads auth.uid() (NULL outside Supabase's request
context).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-10
"""

import re
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

# Opening/closing delimiter of a dollar-quoted body: $$ or $tag$.
_DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


def _split_statements(sql: str) -> list[str]:
    """Split a raw SQL script into complete statements, comments dropped.

    asyncpg prepares every statement (extended query protocol) and PostgreSQL
    rejects multiple commands inside one prepared statement, so each statement
    must be sent to the driver on its own. The scanner understands the three
    constructs that can hide a `;`: dollar-quoted bodies (CREATE FUNCTION
    ... AS $$ ... $$), single-quoted literals (with '' doubling), and comments
    (-- to end of line; /* */ may nest). Statements come back bare — no
    surrounding whitespace, no terminating semicolon, no comments — and
    comment-only stretches are dropped entirely so no empty statement is ever
    executed.
    """
    statements: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    has_code = False  # current chunk holds SQL, not just whitespace

    def flush() -> None:
        nonlocal buf, has_code
        text = "".join(buf).strip()
        if has_code and text:
            statements.append(text)
        buf, has_code = [], False

    while i < n:
        if sql.startswith("--", i):
            newline = sql.find("\n", i)
            i = n if newline == -1 else newline + 1
            continue
        if sql.startswith("/*", i):
            depth, i = 1, i + 2
            while depth and i < n:
                if sql.startswith("/*", i):
                    depth += 1
                    i += 2
                elif sql.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            continue

        ch = sql[i]

        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if sql.startswith("''", j):
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            else:
                j = n
            buf.append(sql[i:j])
            i = j
            has_code = True
            continue

        if ch == "$":
            m = _DOLLAR_TAG.match(sql, i)
            if m:
                tag = m.group(0)
                close = sql.find(tag, i + len(tag))
                j = n if close == -1 else close + len(tag)
                buf.append(sql[i:j])
                i = j
                has_code = True
                continue

        if ch == ";":
            flush()
            i += 1
            continue

        if not ch.isspace():
            has_code = True
        buf.append(ch)
        i += 1

    flush()
    return statements


def _run_script(sql: str) -> None:
    # Online: one exec_driver_sql call per statement (a prepared statement
    # cannot carry multiple commands). Offline (--sql): each statement is
    # emitted into the generated script; alembic appends the `;`.
    offline = context.is_offline_mode()
    for statement in _split_statements(sql):
        if offline:
            op.execute(statement)
        else:
            op.get_bind().exec_driver_sql(statement)


def upgrade() -> None:
    _run_script(SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    # PostgreSQL cannot DROP VALUE from an enum, and policy drops are safe to
    # reverse — RLS itself stays enabled (no data-path change, just denies
    # direct-client access).
    drop_statements = "\n".join(
        f"DROP POLICY IF EXISTS {policy} ON {table};"
        for table, policy in reversed(_POLICIES)
    ) + "\nDROP FUNCTION IF EXISTS public.current_user_has_workspace_access(UUID);"
    _run_script(drop_statements)
