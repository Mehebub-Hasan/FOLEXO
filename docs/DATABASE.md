# FOLEXO — Database Specification (PostgreSQL / Supabase)

| | |
|---|---|
| **Version** | 1.0 |
| **Status** | Approved for Phase 1 baseline |
| **Target** | Supabase PostgreSQL 15+ |
| **Last updated** | 2026-11-07 |
| **Related** | [ARCHITECTURE](ARCHITECTURE.md) · [API](API.md) · [DECISIONS](DECISIONS.md) |

---

## 1. Conventions

| Convention | Rule |
|---|---|
| Primary keys | `uuid` via `gen_random_uuid()` (built-in on PG13+; ADR-006) |
| Foreign keys | Always explicit `references ... on delete cascade` (or `restrict` where noted) |
| Money | `numeric(15,4)` — never `float`/`double` (ADR-007) |
| Quantities | `numeric(12,4)` (unit prices remain `numeric(15,4)`) |
| Confidence | `numeric(4,3)`, `check between 0 and 1` |
| Timestamps | `timestamptz`, default `now()`, UTC only |
| Dates (business) | `date` (invoice/due dates have no time component) |
| Naming | `snake_case` tables/columns; indexes `idx_<table>_<cols>`; uniques `uq_<table>_<cols>`; constraints `ck_<table>_<rule>` |
| Tenancy | Every tenant-scoped table has `tenant_id uuid not null references tenants(id) on delete cascade` as its **first column after the PK** (ADR-005) |
| `updated_at` | Maintained by trigger `set_updated_at()` on every mutable table |
| Migrations | Forward-only SQL files `NNNN_description.sql` in `supabase/migrations/` (§10) |

## 2. Entity overview

```
 profiles ──1:N── tenant_members ──N:1── tenants
                                          │ 1:N (cascade)
     ┌───────────────────────────────────┼──────────────────────────────┐
     │                                   │                              │
 suppliers                        invoices ──1:N── invoice_line_items    audit_logs
     │ 1:N                                   │ 1:N
     └────────►(invoices.supplier_id)        ├── invoice_fields        (per-field, with bbox)
                                             ├── processing_jobs       (pipeline state)
                                             └── extraction_artifacts  (raw vendor payloads)
```

- `profiles` rows are auto-created from `auth.users` by trigger (§7.2); `profiles.id` **is** `auth.users.id`.
- `audit_logs` is tenant-scoped and references entities polymorphously (`entity_type`, `entity_id`) — no FK by design (append-only history survives row deletion).

## 3. Enumerated types

```sql
create type public.tenant_role as enum ('owner', 'admin', 'member', 'viewer');

-- Invoice lifecycle (ARCHITECTURE.md §5.3)
create type public.invoice_status as enum (
  'uploaded', 'processing', 'needs_review', 'ready',
  'approved', 'rejected', 'exported', 'archived', 'failed'
);

create type public.extraction_engine as enum ('azure_di', 'openai_fallback', 'manual');
create type public.job_type         as enum ('extract_invoice');
create type public.job_status       as enum ('queued', 'running', 'succeeded', 'failed');
```

## 4. Core tables

### 4.1 `profiles`

```sql
create table public.profiles (
  id          uuid primary key references auth.users (id) on delete cascade,
  email       text not null unique,
  full_name   text,
  avatar_url  text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
```

### 4.2 `tenants`

```sql
create table public.tenants (
  id                uuid primary key default gen_random_uuid(),
  name              text not null check (char_length(name) between 1 and 120),
  slug              text not null unique
                    check (slug ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$'),
  country_code      char(2) check (country_code ~ '^[A-Z]{2}$'),
  default_currency  char(3) not null default 'USD' check (default_currency ~ '^[A-Z]{3}$'),
  created_by        uuid references public.profiles (id),
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
```

### 4.3 `tenant_members`

```sql
create table public.tenant_members (
  tenant_id  uuid not null references public.tenants (id) on delete cascade,
  user_id    uuid not null references public.profiles (id) on delete cascade,
  role       public.tenant_role not null default 'member',
  invited_by uuid references public.profiles (id),
  created_at timestamptz not null default now(),
  primary key (tenant_id, user_id)
);
```

Invariants enforced in the API layer (not SQL): the founding member is created as `owner`; a tenant cannot lose its last `owner`; role self-promotion is forbidden.

### 4.4 `suppliers`

```sql
create table public.suppliers (
  id                 uuid primary key default gen_random_uuid(),
  tenant_id          uuid not null references public.tenants (id) on delete cascade,
  name               text not null check (char_length(name) between 1 and 200),
  name_norm          text not null,          -- lower(trim(name)); maintained by trigger
  tax_id             text,                   -- VAT / company registration number as printed
  email              text,
  phone              text,
  address_line1      text,
  address_line2      text,
  city               text,
  postal_code        text,
  country_code       char(2) check (country_code ~ '^[A-Z]{2}$'),
  default_currency   char(3) check (default_currency ~ '^[A-Z]{3}$'),
  payment_terms_days smallint check (payment_terms_days between 0 and 365),
  notes              text,
  created_by         uuid references public.profiles (id),
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  constraint uq_suppliers_tenant_name unique (tenant_id, name_norm)
);

-- One tax ID per supplier within a tenant; NULL tax_ids are unconstrained
create unique index uq_suppliers_tenant_tax_id on public.suppliers (tenant_id, tax_id)
  where tax_id is not null;
```

## 5. Invoice tables

### 5.1 `invoices` — system of record for the document + authoritative header values

```sql
create table public.invoices (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants (id) on delete cascade,
  uploaded_by           uuid not null references public.profiles (id),

  -- Source document
  storage_path          text not null,          -- '{tenant_id}/{invoice_id}/{object}'
  original_filename     text not null,
  mime_type             text not null,
  file_size_bytes       bigint not null check (file_size_bytes > 0),
  page_count            integer check (page_count between 1 and 1000),
  content_hash          text not null,          -- sha256 hex of file bytes

  -- Authoritative header fields (post-extraction/post-review values)
  supplier_id           uuid references public.suppliers (id) on delete set null,
  invoice_number        text,
  invoice_date          date,
  due_date              date,
  currency_code         char(3) check (currency_code ~ '^[A-Z]{3}$'),
  po_number             text,
  subtotal_amount       numeric(15,4) check (subtotal_amount >= 0),
  tax_amount            numeric(15,4) check (tax_amount >= 0),
  total_amount          numeric(15,4) check (total_amount >= 0),

  -- Extraction metadata
  extraction_engine     public.extraction_engine,       -- engine of last full pass
  confidence_min        numeric(4,3) check (confidence_min between 0 and 1),
  confidence_avg        numeric(4,3) check (confidence_avg between 0 and 1),
  extraction_started_at timestamptz,
  extraction_finished_at timestamptz,
  is_possible_duplicate boolean not null default false,

  -- Lifecycle (ARCHITECTURE.md §5.3)
  status                public.invoice_status not null default 'uploaded',
  status_changed_at     timestamptz not null default now(),
  rejection_reason      text,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),

  constraint ck_invoices_dates check (due_date is null or invoice_date is null
                                       or due_date >= invoice_date)
);

create index idx_invoices_tenant_created   on public.invoices (tenant_id, created_at desc);
create index idx_invoices_tenant_status    on public.invoices (tenant_id, status);
create index idx_invoices_tenant_supplier  on public.invoices (tenant_id, supplier_id);
create index idx_invoices_tenant_hash      on public.invoices (tenant_id, content_hash);
create index idx_invoices_tenant_number    on public.invoices (tenant_id, invoice_number)
  where invoice_number is not null;
```

Notes:

- **No DB-level unique on `(tenant_id, supplier_id, invoice_number)`**: real duplicates exist (credit notes reusing numbering). Exact file duplicates are caught via `content_hash` at upload (FR-2); same-number/different-file cases are flagged `is_possible_duplicate` by the worker, not blocked.
- Header columns are nullable because they populate asynchronously during extraction.

### 5.2 `invoice_line_items`

```sql
create table public.invoice_line_items (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants (id) on delete cascade,
  invoice_id    uuid not null references public.invoices (id) on delete cascade,
  line_number   integer not null check (line_number between 1 and 10000),
  description   text,
  quantity      numeric(12,4) check (quantity >= 0),
  unit_price    numeric(15,4),
  tax_rate      numeric(6,4) check (tax_rate between 0 and 1),   -- fraction, e.g. 0.2000
  line_total    numeric(15,4),
  page_number   integer,
  confidence    numeric(4,3) check (confidence between 0 and 1),
  source_engine public.extraction_engine,
  is_corrected  boolean not null default false,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint uq_line_items_invoice_line unique (invoice_id, line_number)
);

create index idx_line_items_invoice on public.invoice_line_items (invoice_id, line_number);
```

### 5.3 `invoice_fields` — one row per extracted field, with spatial coordinates

This table implements FR-4 (provenance per field) and the coordinate storage contract of [ARCHITECTURE.md](ARCHITECTURE.md) §7.

```sql
create table public.invoice_fields (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants (id) on delete cascade,
  invoice_id    uuid not null references public.invoices (id) on delete cascade,
  field_key     text not null,   -- 'supplier_name','invoice_number','invoice_date',
                                 -- 'due_date','currency_code','total_amount','po_number',
                                 -- 'line_item:<n>', ...
  raw_text      text,            -- exactly as printed on the document
  value_text    text,            -- normalized string value
  value_number  numeric(15,4),   -- populated for monetary fields
  confidence    numeric(4,3) check (confidence between 0 and 1),
  source_engine public.extraction_engine not null,
  page_number   integer,
  bbox          jsonb,           -- normalized polygon, ARCHITECTURE.md §7; null = no location
  is_corrected  boolean not null default false,
  corrected_by  uuid references public.profiles (id),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint uq_fields_invoice_key unique (invoice_id, field_key)
);

create index idx_fields_invoice          on public.invoice_fields (invoice_id);
create index idx_fields_tenant_key       on public.invoice_fields (tenant_id, field_key);
create index idx_fields_low_confidence   on public.invoice_fields (invoice_id, confidence)
  where is_corrected = false;
```

`bbox` JSONB canonical shape (validation enforced in application layer, documented here):

```json
{
  "unit": "normalized",
  "page_number": 1,
  "polygon": [[0.612, 0.901], [0.874, 0.901], [0.874, 0.943], [0.612, 0.943]]
}
```

### 5.4 `processing_jobs` — pipeline state per invoice

```sql
create table public.processing_jobs (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants (id) on delete cascade,
  invoice_id    uuid not null references public.invoices (id) on delete cascade,
  job_type      public.job_type not null default 'extract_invoice',
  job_status    public.job_status not null default 'queued',
  attempt       integer not null default 0,
  max_attempts  integer not null default 3,
  arq_job_id    text,                        -- broker-side correlation
  error_code    text,                        -- API.md §3 error-code vocabulary
  error_message text,
  engine_used   public.extraction_engine,
  duration_ms   integer,
  queued_at     timestamptz not null default now(),
  started_at    timestamptz,
  finished_at   timestamptz,
  updated_at    timestamptz not null default now()
);

create index idx_jobs_invoice     on public.processing_jobs (invoice_id, queued_at desc);
create index idx_jobs_active      on public.processing_jobs (job_status, queued_at)
  where job_status in ('queued', 'running');
```

### 5.5 `extraction_artifacts` — raw vendor payloads (debugging + accuracy evaluation)

```sql
create table public.extraction_artifacts (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references public.tenants (id) on delete cascade,
  invoice_id  uuid not null references public.invoices (id) on delete cascade,
  stage       text not null check (stage in ('azure_di', 'openai_fallback')),
  attempt     integer not null default 1,
  http_status integer,
  payload     jsonb not null,               -- verbatim provider response (incl. page geometry)
  created_at  timestamptz not null default now()
);

create index idx_artifacts_invoice on public.extraction_artifacts (invoice_id, stage, attempt);
```

Retains per-attempt responses so confidence-gate tuning (ADR-003) can be re-evaluated offline without re-calling providers.

## 6. Audit & stats

### 6.1 `audit_logs` — append-only

```sql
create table public.audit_logs (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references public.tenants (id) on delete cascade,
  actor_id    uuid references public.profiles (id),   -- null = system/worker
  action      text not null,      -- 'invoice.uploaded', 'invoice.approved', 'field.corrected',
                                  -- 'invoice.rejected', 'invoice.reprocessed', 'export.created',
                                  -- 'member.invited', 'member.role_changed', ...
  entity_type text not null,      -- 'invoice', 'supplier', 'tenant', 'membership', ...
  entity_id   uuid,
  metadata    jsonb not null default '{}',
  created_at  timestamptz not null default now()
);

create index idx_audit_tenant_time   on public.audit_logs (tenant_id, created_at desc);
create index idx_audit_entity        on public.audit_logs (tenant_id, entity_type, entity_id);
```

No `updated_at`, no UPDATE/DELETE path in the API; RLS grants only SELECT to members (§8).

## 7. Functions & triggers

### 7.1 `updated_at` maintenance

```sql
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

-- Applied to: profiles, tenants, suppliers, invoices, invoice_line_items,
-- invoice_fields, processing_jobs
create trigger trg_<table>_updated_at before update on public.<table>
  for each row execute function public.set_updated_at();
```

### 7.2 Profile auto-provisioning on signup

```sql
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, email, full_name, avatar_url)
  values (new.id, new.email, new.raw_user_meta_data ->> 'full_name', NULL)
  on conflict (id) do nothing;
  return new;
end $$;

create trigger trg_on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
```

### 7.3 Supplier `name_norm` maintenance

```sql
create or replace function public.set_supplier_name_norm()
returns trigger language plpgsql as $$
begin
  new.name_norm := lower(btrim(new.name));
  return new;
end $$;

create trigger trg_suppliers_name_norm
  before insert or update of name on public.suppliers
  for each row execute function public.set_supplier_name_norm();
```

## 8. Row-Level Security (RLS) strategy

### 8.1 Threat model

Two access paths reach Postgres:

1. **The API/worker with the service role** — bypasses RLS by design; tenant scoping is enforced in the repository layer (`where tenant_id = :tenant_id` on every statement, tenant resolved from verified JWT + membership). This is the primary control (ADR-004/ADR-005).
2. **Direct client access with anon/authenticated keys** (Supabase JS, Storage policies, future Realtime) — RLS is the **only** control here.

RLS therefore exists as defense-in-depth and as the enabler for direct client features; both layers are tested (PRD §9.3).

### 8.2 Helper functions

```sql
create or replace function public.is_tenant_member(t uuid)
returns boolean language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from public.tenant_members m
    where m.tenant_id = t and m.user_id = auth.uid()
  );
$$;

create or replace function public.has_tenant_role(t uuid, allowed public.tenant_role[])
returns boolean language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from public.tenant_members m
    where m.tenant_id = t
      and m.user_id = auth.uid()
      and m.role = any(allowed)
  );
$$;
```

`security definer` avoids recursive RLS evaluation on `tenant_members` itself.

### 8.3 Policy matrix

RLS enabled on **all** tables. Roles referenced below are `tenant_role` values.

| Table | SELECT | INSERT | UPDATE | DELETE |
|---|---|---|---|---|
| `profiles` | own row (`id = auth.uid()`) | — (trigger-created) | own row | — |
| `tenants` | member of tenant | — (service role only) | `owner`, `admin` | — |
| `tenant_members` | member of tenant | — (service role only, via invite flow) | `owner`, `admin` (cannot modify an `owner`; no self-role change) | `owner`, `admin` (not last owner) |
| `suppliers` | member | `owner`,`admin`,`member` | `owner`,`admin`,`member` | `owner`,`admin` |
| `invoices` | member | `owner`,`admin`,`member` | `owner`,`admin`,`member` | `owner`,`admin` |
| `invoice_line_items` | member | — (service role writes) | `owner`,`admin`,`member` (corrections) | — |
| `invoice_fields` | member | — (service role writes) | `owner`,`admin`,`member` (corrections) | — |
| `processing_jobs` | member | — (service role) | — (service role) | — |
| `extraction_artifacts` | `owner`,`admin` | — (service role) | — | — |
| `audit_logs` | member | — (service role) | — | — (append-only) |

Canonical policy pattern (shown for `invoices`; identical shape per table):

```sql
alter table public.invoices enable row level security;

create policy "invoices_select" on public.invoices
  for select using (public.is_tenant_member(tenant_id));

create policy "invoices_insert" on public.invoices
  for insert with check (
    public.has_tenant_role(tenant_id, array['owner','admin','member']::public.tenant_role[])
  );

create policy "invoices_update" on public.invoices
  for update using (
    public.has_tenant_role(tenant_id, array['owner','admin','member']::public.tenant_role[])
  );

create policy "invoices_delete" on public.invoices
  for delete using (
    public.has_tenant_role(tenant_id, array['owner','admin']::public.tenant_role[])
  );
```

### 8.4 RLS test requirement (Phase 2 exit gate)

Automated tests (pgTAP or pytest against a scratch project) MUST assert, for every tenant-scoped table:

1. User A (member of T1) cannot SELECT/UPDATE/DELETE rows of T2.
2. Role gating works (e.g., `viewer` cannot INSERT an invoice).
3. `audit_logs` rejects UPDATE/DELETE even from a tenant `owner`.

## 9. File storage

Private bucket **`invoices`** (no public access, no signed-URL wildcard policies):

- Object path: `{tenant_id}/{invoice_id}/{original_filename}` — tenant prefix enables storage policies:
  - **read**: authenticated user where `(storage.foldername(name))[1] = <tenant they belong to>`
  - **write/delete**: API service role only (all writes flow through the backend).
- Frontend document access goes through `GET /api/v1/invoices/{id}/file`, which returns a short-lived signed URL (≤ 300 s) issued by the backend ([API.md](API.md) §5.7).
- Deletion of an invoice (FR: GDPR posture) deletes: object, `invoices` row (cascade of line items/fields/jobs/artifacts), and retains audit rows (metadata only).

## 10. Migration guidelines

- Location: `supabase/migrations/NNNN_description.sql`, numbered, never renumbered or edited after merge.
- Forward-only: destructive changes (drop column/table) require a new migration plus a documented backfill/grace plan; no down migrations.
- Planned sequence:
  - `0001_init.sql` — extensions n/a, enums, tables, indexes, triggers (§3–§7)
  - `0002_rls.sql` — helper functions + policies (§8)
  - `0003_storage.sql` — bucket + storage policies (§9)
- Apply via `supabase db push` locally and through CI against staging before production; a migration that cannot run on a fresh project from zero is broken.
