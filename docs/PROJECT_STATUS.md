# FOLEXO — Project Status

**Last updated:** 2026-11-08
**Current phase:** Phase 2 — Backend Foundation & Supabase Wiring (step 1 ✅)

This document is the single source of truth for delivery progress. Update it at the *end* of each phase, never mid-phase. Specifications referenced below live in [`docs/`](.).

---

## Phase tracker

| Phase | Scope | Status | Exit criteria | Completed |
|---|---|---|---|---|
| **1** | Documentation & repo scaffolding | ✅ Complete | All six spec docs written and reviewed; directory tree, `.gitignore`, `.env.example`, `README.md` in place | 2026-11-07 |
| **2** | Backend foundation & Supabase wiring *(resequenced: schema is owned by Alembic in `backend/`, not raw SQL in `supabase/migrations/`)* | 🔶 In progress | **Step 1 ✅** backend package (pyproject, config, security, async SQLAlchemy models, FastAPI `/health`), `alembic/0001_initial_schema` verified against model metadata · Step 2: apply `0001` to a real Supabase Postgres, verify types/RLS groundwork; storage bucket + policies |
| **3** | Backend skeleton | ⬜ Not started | FastAPI app boots; `/health`, `/api/v1/me`, tenant endpoints live; Supabase JWT verification middleware with tests; error envelope + request IDs | |
| **4** | Invoice upload & CRUD | ⬜ Not started | Upload → Storage → DB row; list/detail/patch/delete per [`API.md`](API.md); tenant-scoped pagination; content-hash duplicate detection | |
| **5** | Extraction pipeline | ⬜ Not started | Redis + ARQ worker; Stage A (Azure DI) with coordinate persistence; confidence gate + arithmetic validation; Stage B (OpenAI fallback); golden-set accuracy per [`PRD.md`](PRD.md) §9.1 | |
| **6** | Frontend MVP | ⬜ Not started | Auth, upload, review queue, document viewer with bounding-box overlays, approve/reject flows; E2E happy paths pass | |
| **7** | Exports & dashboard | ⬜ Not started | CSV export with penny-perfect validation; dashboard KPIs; audit log UI | |
| **8** | Hardening & launch prep | ⬜ Not started | Full test matrix green; load test meets NFR-1/NFR-5; security review (RLS + authz); CI/CD; staging deploy | |

---

## Definition of done (any phase)

- Code merged with tests; spec deviations documented in the relevant `docs/` file (docs change **first**).
- `docs/PROJECT_STATUS.md` updated with exit-criteria evidence.
- New consequential decisions get an ADR in [`DECISIONS.md`](DECISIONS.md).

## Decision log

All architectural decisions are recorded in [`docs/DECISIONS.md`](DECISIONS.md). Current accepted set: ADR-001 … ADR-008 (no decisions superseded).

## Changelog

- **2026-11-09** — Phase 3 step 2 complete: frontend ingestion UX + invoices inbox — `lib/hash.ts` (Web Crypto SHA-256), `InvoiceUploadModal` (drag-and-drop Dialog, client-side type/size guardrails with sonner toasts, presign → duplicate-confirm prompt → direct XHR upload with live Progress → `POST /invoices` → cache invalidation + success toast), `lib/api.ts` untouched flow types in `lib/api-types.ts`; inbox page (`GET /api/v1/invoices` via `useInfiniteQuery`, color-coded `StatusBadge`, status tabs, empty state, load-more keyset pagination, `/invoices/[id]` placeholder). Backend gained `GET /api/v1/invoices` (tenant-scoped keyset pagination + comma-separated status filter). Backend suite now **27/27** incl. the API-level E2E (presign → confirm → inbox shows `uploaded`). `npm run build` + `npm run lint` clean (10 routes). Notable: disabled Next 16 `cacheComponents` in `next.config.ts` — static prerendering of auth-gated, request-scoped dashboard pages broke every dynamic route (CLIENT_HOOK_DYNAMIC on `usePathname`/`useParams`); browser-level E2E still requires a live Supabase project.
- **2026-11-09** — Phase 3 step 1 complete: two-step ingestion shipped — `POST /api/v1/uploads/presign` (modules/uploads: 15 MB → 413, MIME allow-list pdf/jpeg/png → 415, per-tenant SHA-256 duplicate flag with `existing_invoice_id`, storage path `{org}/{year}/{month}/{uuid}_{clean_filename}`, signed upload URL via a Supabase Storage client seam) and `POST /api/v1/invoices` (201, re-validates constraints, rejects storage paths outside the workspace, creates `document_files` + `invoices(status=uploaded)` + audit entry). 11 new pytest cases (oversize/mime guardrails incl. re-validation, duplicate flag isolation across tenants, tenant-scoped persistence, foreign storage-path rejection) — **25/25 passing** with auth regression. Tests caught the flush-vs-construction PK-default bug again (document id); fixed with explicit UUIDs. Divergences pending doc sync: two-step presign flow replaces multipart upload (API.md), 15 MB/no TIFF, storage path format.
- **2026-11-08** — Phase 2 step 3 complete: frontend foundation scaffolded (`create-next-app`: Next.js 16.4.5, React 19, TypeScript, Tailwind v4; shadcn/ui radix/nova preset with Button, Input, Card, Table, DropdownMenu, Dialog, Tabs, Badge + lucide-react). Centralized branding (`src/config/branding.ts` — no hardcoded brand names in components); Supabase browser/server clients (`@supabase/ssr`) + auth middleware protecting the (dashboard) group with session refresh; `/login` (password + magic link) and `/register` with authenticated-user bounce to `/dashboard`; dashboard shell (collapsible responsive sidebar, workspace switcher over `GET /api/v1/auth/me` backed by localStorage + `useSyncExternalStore`, profile avatar + logout); axios client auto-attaching `Authorization: Bearer` + `X-Workspace-ID`; React Query providers. Verified: `npm run build` clean (9 routes, zero TS errors), `npm run lint` clean, live smoke test (`/dashboard` → 307 `/login?next=/dashboard`). Note: Next 16 deprecates `middleware.ts` in favor of `proxy.ts` — kept per spec, codemod available.
- **2026-11-08** — Phase 2 step 2 complete: Supabase JWT verification (HS256 + RS256 public-key support, iss/aud/exp validation, `UserPayload{id, email}`) in `core/security.py`; `app/api/deps.py` guardrails (`get_current_user` → 401; `get_current_workspace` via `X-Workspace-ID` + membership query → 403 "Access to this workspace is forbidden."); `api/v1/workspaces.py` (`GET /auth/me`, `POST /workspaces` with atomic owner enrollment, `GET /workspaces/current` guardrail probe); router mounted under `/api/v1`. Test suite: 14 async pytest cases (401 family, tenant isolation 403, workspace creation, slug conflicts) against in-process SQLite with the real `get_session` overridden — **14/14 passing**. Tests caught two real bugs: membership built before flush (NULL PK) and flush outside the IntegrityError guard. `X-Workspace-ID` supersedes docs' `X-Tenant-Id` — doc sync still pending.
- **2026-11-08** — Phase 2 step 1 complete: `backend/` package initialized (pyproject, pydantic-settings config, security helpers, async SQLAlchemy 2 models for 8 tables, FastAPI app factory with `/health`); Alembic configured async (asyncpg) with `0001_initial_schema` migration; verified: imports cycle-free, `uvicorn` boot + `GET /health` → `{"status": "ok"}`, offline SQL compile, and a programmatic migration ↔ model-metadata consistency check (all columns/constraints/indexes covered). Naming divergences from docs/DATABASE.md (organizations/vendors/invoice_items/document_files/extraction_runs) recorded for doc sync.
- **2026-11-07** — Phase 1 complete: authored PRD, ARCHITECTURE, DATABASE, API, DECISIONS (ADR-001…008), PROJECT_STATUS; scaffolded `docs/`, `frontend/`, `backend/`, `supabase/migrations/`; created `.gitignore`, `.env.example`, `README.md`.

## Next up — Phase 2 backlog

1. Create the Supabase project; record region and instance sizing decision.
2. Materialize `supabase/migrations/0001_init.sql` from [`DATABASE.md`](DATABASE.md) §4–§7 (enums, tables, indexes, triggers).
3. Add `0002_rls.sql` with helper functions and policy matrix from [`DATABASE.md`](DATABASE.md) §8.
4. Create the private `invoices` storage bucket + storage policies ([`DATABASE.md`](DATABASE.md) §9).
5. Verify: fresh-project migration run, RLS cross-tenant smoke test, storage policy test.
