# FOLEXO — System Architecture

| | |
|---|---|
| **Version** | 1.0 |
| **Status** | Approved for Phase 1 baseline |
| **Last updated** | 2026-11-07 |
| **Related** | [PRD](PRD.md) · [DATABASE](DATABASE.md) · [API](API.md) · [DECISIONS](DECISIONS.md) |

---

## 1. Architecture principles

1. **Modular monolith first** (ADR-001) — one deployable API process plus one worker process, internally partitioned into modules with enforced boundaries. Services are extracted later only if a boundary proves it needs independent scaling.
2. **Async-first for anything slow** — every operation that touches an external AI provider or processes a file runs on the Redis + ARQ pipeline (ADR-002); the API never blocks on extraction.
3. **Extract, then verify** — AI output is never silently trusted: every field carries confidence + source attribution, arithmetic validation gates the status, and the expensive fallback stage runs only when justified (ADR-003).
4. **Tenant isolation by default** — every table is tenant-scoped, every query is tenant-filtered, and PostgreSQL RLS backs the API layer up (ADR-005).
5. **Money is `NUMERIC`, coordinates are normalized, IDs are UUIDs** — data conventions are fixed in [DATABASE.md](DATABASE.md) and never negotiated per feature.

## 2. System context

```
                        ┌──────────────────────────────────────────┐
                        │                Users                     │
                        │  (AP clerk · finance manager · admin)    │
                        └───────────────┬──────────────────────────┘
                                        │ HTTPS (Bearer JWT)
                                        ▼
┌────────────────────┐   ┌──────────────────────────────────────────┐
│ Supabase Auth      │◄──┤            FOLEXO SaaS                   │
│ (JWT issuance)     │   │                                          │
└────────────────────┘   │  Next.js frontend ──► FastAPI monolith    │
                         │                          │        │      │
┌────────────────────┐   │                          │        │      │
│ Azure Document     │◄──┼──────────────────────────┘        │      │
│ Intelligence       │   │         (Stage A, from worker)    │      │
└────────────────────┘   │                                   ▼      ▼
┌────────────────────┐   │                     ┌──────────┐ ┌─────────┐
│ OpenAI             │◄──┼──────────────────── │  Redis   │ │Supabase │
│ (Stage B fallback) │   │      (Stage B)      │  (ARQ)   │ │Postgres │
└────────────────────┘   │                     └────┬─────┘ │ Storage │
                         │                          │       └─────────┘
                         │                     ARQ worker    ▲
                         └───────────────────────────────────┘
```

## 3. Components

| Component | Technology (planned) | Responsibility |
|---|---|---|
| **Frontend** | Next.js (App Router) + TypeScript + Tailwind; Supabase JS for auth; TanStack Query for server state | Upload UI, review queue, document viewer with bounding-box overlays, approvals, admin |
| **API (monolith)** | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 async + asyncpg | Auth verification, tenant resolution, CRUD, upload orchestration, validation, export |
| **Worker** | ARQ (asyncio) consuming Redis | Stage A/B extraction, validation, persistence of fields/line items, status transitions |
| **Broker** | Redis | Job queue for ARQ; single queue `invoice:extract` in MVP |
| **Database** | Supabase PostgreSQL 15 | System of record; RLS defense-in-depth ([DATABASE.md](DATABASE.md)) |
| **File storage** | Supabase Storage, private `invoices` bucket | Source documents only; derived data lives in Postgres |
| **Auth** | Supabase Auth | Magic-link/email OTP + password; issues HS256 JWTs; FastAPI verifies locally (ADR-004) |
| **Azure Document Intelligence** | `prebuilt-invoice` | Stage A extraction engine (fields, line items, confidence, bounding regions) |
| **OpenAI** | Structured outputs (JSON schema) | Stage B conditional fallback engine (ADR-003) |

## 4. Module boundaries inside the monolith

One process, strict internal seams. Each module owns its tables and exposes only its service interface to other modules; cross-module reads go through the owning module's service, never direct table joins in foreign modules' repositories.

```
backend/
├── app/
│   ├── core/                     # shared kernel — no business logic
│   │   ├── config.py             # env-driven settings (pydantic-settings)
│   │   ├── db.py                 # async engine/session, RLS-aware helpers
│   │   ├── security.py           # Supabase JWT verification (ADR-004)
│   │   ├── errors.py             # error envelope + exception handlers (API.md §3)
│   │   ├── logging.py            # structured JSON logs, request IDs
│   │   └── money.py              # NUMERIC parsing/formatting helpers
│   ├── modules/
│   │   ├── auth/                 # JWT dependency, current-user, tenant resolution
│   │   ├── tenants/              # tenants, memberships, roles, invites
│   │   ├── suppliers/            # supplier directory, matching, dedup
│   │   ├── invoices/             # upload, CRUD, status machine, review corrections
│   │   ├── extraction/           # pipeline orchestration: enqueue, stage A, gate, stage B, merge
│   │   ├── audit/                # append-only audit event writer (used by all modules)
│   │   └── exports/              # CSV export generation
│   ├── workers/
│   │   ├── settings.py           # ARQ worker settings (retries, backoff, timeouts)
│   │   └── tasks/
│   │       └── extract_invoice.py
│   └── main.py                   # FastAPI app assembly
```

**Dependency rule:** `workers/` and `modules/extraction` may depend on `invoices`, `suppliers`, `audit`; nothing depends on `extraction` except the upload endpoint enqueueing a job (via a thin queue interface, so the broker is swappable — see ADR-002 consequences).

## 5. Async pipeline (Redis + ARQ)

### 5.1 Flow

```
Frontend           API (invoices module)           Redis            ARQ Worker
   │  POST /invoices    │                          │                  │
   │──────────────────►│  1. validate + hash file  │                  │
   │                    │  2. put file in Storage   │                  │
   │                    │  3. INSERT invoices       │                  │
   │                    │     (status=uploaded)     │                  │
   │                    │  4. INSERT processing_jobs│                  │
   │                    │  5. enqueue ─────────────►│  invoice:extract │
   │◄── 202 {invoice,job}                          │                  │
   │                    │                          │  6. dequeue ─────►
   │  GET /invoices/{id} (poll, ~2 s interval)     │        7. status=processing
   │──────────────────►│                          │        8. Stage A (Azure DI)
   │                    │                          │        9. confidence gate
   │                    │                          │        10. Stage B (only if gate fails)
   │                    │                          │        11. arithmetic validation
   │                    │                          │        12. persist fields/items/artifacts
   │                    │                          │        13. status=ready | needs_review | failed
   │◄── {status, ...}   │                          │
```

The frontend polls `GET /api/v1/invoices/{id}` (~2 s interval, stops on terminal status). Supabase Realtime is a post-MVP upgrade path; the API shape does not change when it lands.

### 5.2 Job semantics

| Property | Value |
|---|---|
| Queue | `invoice:extract` (single queue MVP) |
| Job record | `processing_jobs` row created **before** enqueue (audit + status source of truth; Redis is not durable state) |
| Attempts | 3, exponential backoff (10 s base), on transient provider errors (5xx, timeout, rate limit) |
| Hard failure | Schema/validation-level failures mark the job `failed` and invoice `failed` with `error_code` (`EXTRACTION_FAILED`, `UNSUPPORTED_DOCUMENT`, `PROVIDER_OUTAGE`) |
| Timeout | Stage A ≤ 90 s, Stage B ≤ 60 s, whole job ≤ 5 min (ARQ `job_timeout`) |
| Idempotency | Worker steps keyed by `invoice_id`; re-running a job overwrites machine-produced rows (`invoice_fields` where `is_corrected = false`, line items, artifacts) and never manual corrections |
| Poison pills | After max attempts the job dead-ends as `failed`; no infinite requeue |

### 5.3 Status machine (authoritative — enforced by API + worker)

```
 uploaded ──► processing ──► ready ─────────► approved ──► exported ──► archived
                 │  ▲            │                              ▲
                 │  │            ├──► needs_review ──► ready ────┤
                 │  │            │        │              └──────► rejected ──► archived
                 │  │            │        └──► approved / rejected
                 │  │            │
                 │  └── reprocess│(from failed / needs_review / ready)
                 ▼               ▼
               failed ◄──────────┘
```

Transitions are validated server-side; illegal transitions return `409 CONFLICT_STATE` ([API.md](API.md) §6). `needs_review → ready` happens automatically when a correction batch passes arithmetic validation (FR-7).

## 6. Dual-stage extraction design (ADR-003)

### 6.1 Stage A — Azure Document Intelligence (primary)

- Model: `prebuilt-invoice` (purpose-trained for supplier invoices).
- Input: the stored document (SAS URL or bytes) directly to DI — no API-side OCR.
- Output captured per field: printed text, normalized value, confidence, page number, **bounding polygon** (DI `boundingRegions` / `spans`), plus full line-item array.
- Raw response stored verbatim in `extraction_artifacts(stage='azure_di')` for debugging and accuracy evaluation.

### 6.2 Confidence gate (the conditional in "conditional fallback")

Run Stage B **only when** any of:

1. A critical field (`supplier_name`, `invoice_number`, `invoice_date`, `due_date`, `total_amount`, `currency_code`) has confidence < `EXTRACTION_CONFIDENCE_THRESHOLD` (default **0.85**), or
2. A critical field is missing entirely, or
3. Arithmetic validation fails (`subtotal + tax ≠ total`, or Σ line totals ≠ subtotal — tolerance 0.01 in invoice currency).

Otherwise Stage B is skipped: invoice goes straight through validation → `ready` or `needs_review`. This is the cost/latency win over a double-call pipeline (ADR-003).

### 6.3 Stage B — OpenAI (conditional fallback)

- Prompt payload: DI's raw field JSON + the document's text layer (or OCR text for scans), **never the raw image** in MVP.
- Output: constrained structured output (JSON schema) mirroring the same field set — the model reconciles/repairs DI output, it does not free-form.
- Every field produced by Stage B is stored with `source_engine = 'openai_fallback'`, and **the invoice is forced to `needs_review` regardless of self-reported confidence** — LLM confidence is not trusted (PRD §11).

### 6.4 Merge & persistence rules

| Situation | Resolution |
|---|---|
| Gate passed | Stage A fields persisted as-is |
| Gate failed, Stage B ran | Stage B value wins for fields it returned; Stage A values retained for the rest; every Stage B field keeps `needs_review` |
| Arithmetic still fails post-fallback | `needs_review` with validation metadata on the invoice |
| Worker crash mid-job | Retry from Stage A (jobs are idempotent; artifacts overwritten per attempt) |

## 7. Spatial coordinate storage

Extraction coordinates exist so the frontend can draw an outline around exactly where a field was found on the source document (review flow, PRD Flow B).

**Canonical shape** — `invoice_fields.bbox` (JSONB):

```json
{
  "unit": "normalized",
  "page_number": 1,
  "polygon": [[0.612, 0.901], [0.874, 0.901], [0.874, 0.943], [0.612, 0.943]]
}
```

Rules:

- Coordinates are **normalized to page dimensions** (`x/width`, `y/height`, range 0–1), origin at **top-left**, polygon vertices in **clockwise** order, minimum 3 vertices. Normalization makes coordinates render-resolution-independent: `pixel_x = x × rendered_width_px`.
- DI's pixel/point-based regions are converted to normalized form at ingestion time; the conversion factors (page width/height per page) are kept inside the raw artifact in `extraction_artifacts.payload`, so re-normalization is possible without re-calling the provider.
- Line items store page + confidence on the row (`invoice_line_items.page_number`) and, when DI provides them, per-item polygons via `invoice_fields.field_key = 'line_item:<n>'` entries.
- `bbox` is nullable: fields injected without a document location (e.g., manual corrections, matched supplier metadata) have no coordinates by design; the frontend renders overlays only where `bbox` is non-null.

## 8. Auth & tenancy flow

```
Frontend                    Supabase Auth                FastAPI                     Postgres
   │  sign-in (magic link /     │                          │                           │
   │  password via supabase-js)│                          │                           │
   │──────────────────────────►│  issues HS256 JWT        │                           │
   │◄── access_token ──────────│                          │                           │
   │  API call                 │                          │                           │
   │  Authorization: Bearer …  │                          │                           │
   │  X-Tenant-Id: <uuid>      │                          │                           │
   │────────────────────────────────────────────────────►│  1. verify JWT signature  │
   │                            │                         │     + exp (local, no call)│
   │                            │                         │  2. load membership       │
   │                            │                         │     (user_id, tenant_id)  │
   │                            │                         │──────────────────────────►│
   │                            │                         │  3. role → permissions    │
   │                            │                         │  4. queries filtered by   │
   │                            │                         │     tenant_id; runs with  │
   │                            │                         │     service-role DSN      │
```

Key properties (ADR-004):

- **Verification is local**: FastAPI verifies the HS256 signature with `SUPABASE_JWT_SECRET` — no network call to Supabase per request.
- **Tenant context is database truth**: the `X-Tenant-Id` header is accepted only if a `tenant_members` row exists for the JWT subject; otherwise `403 FORBIDDEN_TENANT`. The membership (role included) is cached per request, never per header claim.
- **RLS is defense-in-depth**: the API writes through the service role (which bypasses RLS) but every query is explicitly tenant-filtered in the repository layer; RLS protects any path that reaches Postgres outside the API (Supabase JS direct reads, Storage policies, future Realtime).

## 9. Environments & deployment topology (target)

| Environment | Frontend | API + Worker | Data |
|---|---|---|---|
| Local dev | `localhost:3000` | uvicorn + `arq` processes via Docker Compose (Redis container) | Local or hosted Supabase project |
| Staging | Vercel preview | Single API instance + 1 worker (Fly.io/Railway container) | Dedicated Supabase project |
| Production | Vercel | API ≥ 2 replicas, worker scaled on queue depth (HPA) | Production Supabase project (PITR enabled) |

Config is env-var driven only (`.env.example`); no config files per environment.

## 10. Observability

- **Logs**: structured JSON, one line per request/job, with `request_id` (API) or `job_id` (worker) propagated; correlation from upload → stages via `invoice_id`.
- **Metrics** (Phase 8 hardening, but schema-ready now): extraction duration per stage, fallback trigger rate, per-field confidence distributions, queue depth, job failure rate by `error_code`.
- **Extraction-quality dashboard**: `extraction_artifacts` + `invoice_fields.source_engine` + correction events give the full accuracy feedback loop (which engine produced a field, did a human change it) — the dataset for future prompt/model tuning.

## 11. Evolution path (deliberate seams)

- **Queue abstraction**: `modules/extraction` talks to a thin enqueue interface; swapping ARQ for another broker is an adapter change (ADR-002).
- **Service extraction candidates** (only when metrics demand): the worker (CPU/provider-bound, scales differently than API) and the export module (bulk jobs).
- **Realtime updates**: Supabase Realtime can replace polling without API contract changes.
- **Model upgrades**: engines are configured per stage; a new Stage B model is a settings change plus golden-set regression run (PRD §9.1).
