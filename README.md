# FOLEXO — Invoice AI

**FOLEXO** is a multi-tenant SaaS application that automates **Accounts Payable (AP)** for small and mid-sized teams: drop supplier invoices in (PDF or image), and get structured, verified, export-ready data out — with per-field confidence scores and on-document spatial highlighting for fast human review.

> **Current status: Phase 1 — Documentation & Repository Scaffolding (complete).**
> No application code exists yet by design. All engineering is specified first; see [`docs/`](#documentation) below. Phase tracking lives in [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md).

---

## Product vision

Manual invoice keying costs SMBs minutes per document, produces payment errors, and buries finance teams in review queues. FOLEXO replaces that with an async, AI-first pipeline:

1. **Upload** — a clerk drags invoices into the review queue (PDF, PNG, JPEG, TIFF).
2. **Extract** — an async worker runs Azure Document Intelligence (`prebuilt-invoice`) as the primary engine; if critical-field confidence falls below threshold, an OpenAI fallback stage reconciles the result using structured-output constraints.
3. **Review** — the reviewer sees every extracted field with its confidence score and bounding-box overlay on the original document; corrections are one keystroke away.
4. **Approve & export** — validated invoices flow through an approval step into CSV export for the accounting stack, with a full audit trail.

## Repository structure

```
.
├── docs/                    # Specification documents — the source of truth (Phase 1)
│   ├── PRD.md               # Product requirements, personas, MVP scope, acceptance criteria
│   ├── ARCHITECTURE.md      # Modular monolith, async pipeline, dual-stage extraction
│   ├── DATABASE.md          # PostgreSQL schema, indexes, RLS strategy
│   ├── API.md               # Versioned REST contract (v1)
│   ├── DECISIONS.md         # Architectural Decision Records (ADRs)
│   └── PROJECT_STATUS.md    # Phase tracking document
├── frontend/                # Next.js 16 + TypeScript + Tailwind v4 (foundation: Phase 2, step 3)
│   └── src/                 # Auth (Supabase), dashboard shell, workspace context, API client
├── backend/                 # FastAPI monolith (Phases 2–3) — auth guardrails + workspaces live
├── supabase/
│   └── migrations/          # Forward-only SQL migrations (Phase 2)
├── .env.example             # Environment template — copy to .env, never commit .env
├── .gitignore               # Node + Python + env + OS rules
└── README.md
```

## Documentation

| Document | Purpose |
|---|---|
| [`docs/PRD.md`](docs/PRD.md) | Product vision, user personas, MVP scope boundaries (AP focus), acceptance criteria |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System design: modular monolith, Redis + ARQ async pipeline, dual-stage extraction, spatial coordinate storage |
| [`docs/DATABASE.md`](docs/DATABASE.md) | Complete PostgreSQL schema: UUID keys, `NUMERIC(15,4)` financial fields, multi-tenant tables, indexes, RLS strategy |
| [`docs/API.md`](docs/API.md) | Complete versioned REST API contract for v1: schemas, status codes, error models |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | ADRs: ARQ vs Celery, Azure-first conditional LLM fallback, Supabase Auth + FastAPI verification, and more |
| [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) | Phase tracker — initialized at Phase 1 |

## Planned tech stack

| Layer | Choice | Rationale in |
|---|---|---|
| Frontend | Next.js (App Router) + TypeScript + Tailwind | `docs/ARCHITECTURE.md` §3 |
| Backend API | Python 3.12 + FastAPI, modular monolith | ADR-001 |
| Async pipeline | Redis + ARQ worker | ADR-002 |
| Extraction | Azure Document Intelligence (`prebuilt-invoice`) primary → OpenAI structured-output fallback | ADR-003 |
| Auth | Supabase Auth (JWT) verified in FastAPI | ADR-004 |
| Database | Supabase PostgreSQL 15, shared-schema multi-tenancy + RLS | ADR-005, `docs/DATABASE.md` |
| File storage | Supabase Storage (private bucket) | `docs/DATABASE.md` §9 |

## Getting started (Phase 2+)

Nothing to run yet. Once Phase 2 lands:

```bash
cp .env.example .env      # fill in Supabase, Redis, Azure DI, and OpenAI credentials
```

Credentials needed (all placeholders present in `.env.example`):
- **Supabase** — project URL, anon key, service-role key, JWT secret, database URL
- **Redis** — connection URL for the ARQ broker
- **Azure Document Intelligence** — endpoint + API key
- **OpenAI** — API key for the fallback extraction stage

## Workflow

- Database changes ship as forward-only SQL files in `supabase/migrations/` (`NNNN_description.sql`).
- Specification changes update `docs/` first; implementation PRs reference the doc section they implement.
- Phase completion is recorded in `docs/PROJECT_STATUS.md`.
