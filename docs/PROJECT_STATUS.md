# FOLEXO — Project Status

**Last updated:** 2026-11-07
**Current phase:** Phase 1 — Documentation & Repository Scaffolding ✅ **complete**

This document is the single source of truth for delivery progress. Update it at the *end* of each phase, never mid-phase. Specifications referenced below live in [`docs/`](.).

---

## Phase tracker

| Phase | Scope | Status | Exit criteria | Completed |
|---|---|---|---|---|
| **1** | Documentation & repo scaffolding | ✅ Complete | All six spec docs written and reviewed; directory tree, `.gitignore`, `.env.example`, `README.md` in place | 2026-11-07 |
| **2** | Supabase foundations | ⬜ Not started | `supabase/migrations/0001_init.sql` materialized from [`DATABASE.md`](DATABASE.md); applies cleanly to a fresh Supabase project; storage bucket + policies created; RLS smoke-tested | |
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

- **2026-11-07** — Phase 1 complete: authored PRD, ARCHITECTURE, DATABASE, API, DECISIONS (ADR-001…008), PROJECT_STATUS; scaffolded `docs/`, `frontend/`, `backend/`, `supabase/migrations/`; created `.gitignore`, `.env.example`, `README.md`.

## Next up — Phase 2 backlog

1. Create the Supabase project; record region and instance sizing decision.
2. Materialize `supabase/migrations/0001_init.sql` from [`DATABASE.md`](DATABASE.md) §4–§7 (enums, tables, indexes, triggers).
3. Add `0002_rls.sql` with helper functions and policy matrix from [`DATABASE.md`](DATABASE.md) §8.
4. Create the private `invoices` storage bucket + storage policies ([`DATABASE.md`](DATABASE.md) §9).
5. Verify: fresh-project migration run, RLS cross-tenant smoke test, storage policy test.
