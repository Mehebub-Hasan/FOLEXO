-- ============================================================
-- FOLEXO — Row-Level Security (Phase 7 step 2)
-- ============================================================
-- Defense-in-depth layer (ADR-009): the FastAPI API already verifies the
-- Supabase JWT, resolves workspace membership on every request, and filters
-- every query by organization_id. These policies constrain the OTHER access
-- path: direct Supabase-client queries (anon/authenticated roles, storage
-- policies, future Realtime).
--
-- APPLY PATH: this file is executed verbatim by Alembic revision 0005
-- (backend/alembic/versions/0005_row_level_security.py) — `alembic upgrade
-- head`. Do not apply it separately.
--
-- ROLE SCOPING: every policy is TO `authenticated`. That is deliberate:
-- policies without a role clause would also bind the `postgres` role the
-- API/worker/migrations connect as, breaking every backend query. The
-- `service_role` key used for privileged operations has BYPASSRLS.
-- ============================================================

-- ── Helper ───────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.current_user_has_workspace_access(lookup_org_id UUID)
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, auth
AS $$
  SELECT EXISTS (
    SELECT 1 FROM organization_members
    WHERE organization_id = lookup_org_id AND user_id = auth.uid()
  );
$$;

-- ── Enable RLS on every application table ────────────────────────────────

ALTER TABLE organizations        ENABLE ROW LEVEL SECURITY;
ALTER TABLE organization_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendors              ENABLE ROW LEVEL SECURITY;
ALTER TABLE invoices             ENABLE ROW LEVEL SECURITY;
ALTER TABLE invoice_items        ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_files       ENABLE ROW LEVEL SECURITY;
ALTER TABLE extraction_runs      ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs           ENABLE ROW LEVEL SECURITY;

-- ── organizations: read-only for members (mutations flow through the API's
--    service role; the founding-member invariant is API-layer logic) ──────

CREATE POLICY organizations_select ON organizations
  FOR SELECT TO authenticated
  USING (public.current_user_has_workspace_access(id));

-- ── organization_members: members can read fellow members ────────────────

CREATE POLICY organization_members_select ON organization_members
  FOR SELECT TO authenticated
  USING (public.current_user_has_workspace_access(organization_id));

-- ── Tenant-scoped business tables: full CRUD within own workspace ────────

CREATE POLICY vendors_select ON vendors
  FOR SELECT TO authenticated
  USING (public.current_user_has_workspace_access(organization_id));
CREATE POLICY vendors_insert ON vendors
  FOR INSERT TO authenticated
  WITH CHECK (public.current_user_has_workspace_access(organization_id));
CREATE POLICY vendors_update ON vendors
  FOR UPDATE TO authenticated
  USING (public.current_user_has_workspace_access(organization_id))
  WITH CHECK (public.current_user_has_workspace_access(organization_id));
CREATE POLICY vendors_delete ON vendors
  FOR DELETE TO authenticated
  USING (public.current_user_has_workspace_access(organization_id));

CREATE POLICY invoices_select ON invoices
  FOR SELECT TO authenticated
  USING (public.current_user_has_workspace_access(organization_id));
CREATE POLICY invoices_insert ON invoices
  FOR INSERT TO authenticated
  WITH CHECK (public.current_user_has_workspace_access(organization_id));
CREATE POLICY invoices_update ON invoices
  FOR UPDATE TO authenticated
  USING (public.current_user_has_workspace_access(organization_id))
  WITH CHECK (public.current_user_has_workspace_access(organization_id));
CREATE POLICY invoices_delete ON invoices
  FOR DELETE TO authenticated
  USING (public.current_user_has_workspace_access(organization_id));

CREATE POLICY invoice_items_select ON invoice_items
  FOR SELECT TO authenticated
  USING (public.current_user_has_workspace_access(organization_id));
CREATE POLICY invoice_items_insert ON invoice_items
  FOR INSERT TO authenticated
  WITH CHECK (public.current_user_has_workspace_access(organization_id));
CREATE POLICY invoice_items_update ON invoice_items
  FOR UPDATE TO authenticated
  USING (public.current_user_has_workspace_access(organization_id))
  WITH CHECK (public.current_user_has_workspace_access(organization_id));
CREATE POLICY invoice_items_delete ON invoice_items
  FOR DELETE TO authenticated
  USING (public.current_user_has_workspace_access(organization_id));

CREATE POLICY document_files_select ON document_files
  FOR SELECT TO authenticated
  USING (public.current_user_has_workspace_access(organization_id));
CREATE POLICY document_files_insert ON document_files
  FOR INSERT TO authenticated
  WITH CHECK (public.current_user_has_workspace_access(organization_id));
CREATE POLICY document_files_update ON document_files
  FOR UPDATE TO authenticated
  USING (public.current_user_has_workspace_access(organization_id))
  WITH CHECK (public.current_user_has_workspace_access(organization_id));
CREATE POLICY document_files_delete ON document_files
  FOR DELETE TO authenticated
  USING (public.current_user_has_workspace_access(organization_id));

CREATE POLICY extraction_runs_select ON extraction_runs
  FOR SELECT TO authenticated
  USING (public.current_user_has_workspace_access(organization_id));
CREATE POLICY extraction_runs_insert ON extraction_runs
  FOR INSERT TO authenticated
  WITH CHECK (public.current_user_has_workspace_access(organization_id));
CREATE POLICY extraction_runs_update ON extraction_runs
  FOR UPDATE TO authenticated
  USING (public.current_user_has_workspace_access(organization_id))
  WITH CHECK (public.current_user_has_workspace_access(organization_id));
CREATE POLICY extraction_runs_delete ON extraction_runs
  FOR DELETE TO authenticated
  USING (public.current_user_has_workspace_access(organization_id));

-- ── audit_logs: append-only — SELECT for members, INSERT via clients is
--    allowed with a workspace check; NO update/delete policies exist, so
--    those operations are denied by default (docs/DATABASE.md §6.1) ───────

CREATE POLICY audit_logs_select ON audit_logs
  FOR SELECT TO authenticated
  USING (public.current_user_has_workspace_access(organization_id));
CREATE POLICY audit_logs_insert ON audit_logs
  FOR INSERT TO authenticated
  WITH CHECK (public.current_user_has_workspace_access(organization_id));
