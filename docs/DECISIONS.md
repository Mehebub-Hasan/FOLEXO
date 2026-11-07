# FOLEXO — Architectural Decision Records (ADRs)

Format: lightweight ADR — **Status / Context / Decision / Alternatives considered / Consequences**.
ADRs are immutable once Accepted; to change a decision, write a new ADR that supersedes the old one and update the status line.

| ADR | Title | Status |
|---|---|---|
| [ADR-001](#adr-001) | Modular monolith over microservices | Accepted |
| [ADR-002](#adr-002) | ARQ over Celery for the async pipeline | Accepted |
| [ADR-003](#adr-003) | Azure-first extraction with **conditional** OpenAI fallback | Accepted |
| [ADR-004](#adr-004) | Supabase Auth with FastAPI-side token verification | Accepted |
| [ADR-005](#adr-005) | Shared-schema multi-tenancy with PostgreSQL RLS | Accepted |
| [ADR-006](#adr-006) | UUID primary keys | Accepted |
| [ADR-007](#adr-007) | `NUMERIC(15,4)` for all monetary values | Accepted |
| [ADR-008](#adr-008) | Spatial coordinates stored as normalized JSONB polygons | Accepted |

All dated **2026-11-07**. Deciders: founding team.

---

## ADR-001 — Modular monolith over microservices

**Status:** Accepted · 2026-11-07

### Context
FOLEXO has a small founding team, an unvalidated product, and one genuinely heavy workload (extraction) that is already isolated by the async boundary (API enqueues, worker executes). Microservices would buy deployment independence we do not yet need, at the price of distributed transactions, versioned inter-service contracts, and per-service ops burden.

### Decision
Ship a single deployable **API process** (FastAPI) plus one **worker process** (ARQ), internally partitioned into modules with enforced dependency rules ([ARCHITECTURE.md](ARCHITECTURE.md) §4). Cross-module access goes through service interfaces, never direct foreign-table queries.

### Alternatives considered
- **Microservices from day one** — rejected: premature; the extraction "service" already exists de facto as the worker; the rest has no independent scaling profile yet.
- **Serverless functions per operation** — rejected: long-running extraction stages exceed function time limits; state coordination across providers (DI → gate → OpenAI → DB) is awkward and harder to observe.

### Consequences
- (+) One deploy, one test suite, local dev via Docker Compose; fastest iteration for Phase 3–6.
- (+) The API↔worker split is the natural first extraction seam if load profiles diverge.
- (−) Module boundary discipline depends on review enforcement; mitigated by the dependency rule and (Phase 8) an import-linter check in CI.
- (neutral) If a module must become a service, its service interface is already the contract.

---

## ADR-002 — ARQ over Celery for the async pipeline

**Status:** Accepted · 2026-11-07

### Context
The extraction pipeline (Stage A → confidence gate → conditional Stage B → validation → persistence) is fully `asyncio` I/O: HTTP calls to Azure DI and OpenAI, async DB writes. It needs retries, timeouts, and a durable-enough queue. The backend is a Python/FastAPI monolith (ADR-001), Redis is already required as the broker.

### Decision
Use **ARQ** (redis-based asyncio task queue) for the worker, behind a thin enqueue interface owned by `modules/extraction`.

### Alternatives considered
- **Celery** — the incumbent with the big ecosystem (Canvas workflows, monitoring integrations, beat). Rejected for this codebase: its worker model is sync/prefork; running async pipeline code under Celery means `asgiref`/gevent shims or blocking wrappers that forfeit the async I/O advantage and complicate testing. Its operational surface (result backends, beat schedules, broker flexibility) is capability we would pay for and not use. Features we actually need — retries with backoff, job timeouts, scheduled jobs — ARQ provides natively.
- **Dramatiq / RQ** — same sync-worker mismatch as Celery (RQ additionally lacks native asyncio).
- **Managed queues (SQS + Lambda, Cloud Tasks)** — rejected for MVP: provider lock-in before product-market fit; local dev parity suffers; revisit only if queue ops become a burden.
- **In-process BackgroundTasks (FastAPI)** — rejected outright: no durability, no retries, dies with the request process; unacceptable for a money-adjacent pipeline.

### Consequences
- (+) One mental model end-to-end: `async def` from route to DB write; no shim layers.
- (+) Minimal dependency and ops footprint; Redis is the only new infra piece.
- (+) ARQ's per-job timeout/retry map 1:1 onto [ARCHITECTURE.md](ARCHITECTURE.md) §5.2 semantics.
- (−) Smaller ecosystem: no Celery-flower equivalent; mitigated by `processing_jobs` being the durable source of truth (Redis holds only queued work) and by job metrics planned in Phase 8.
- (−) Single-Redis-broker scaling ceiling is far beyond MVP volume; the enqueue interface keeps a swap to Celery/SQS an adapter change, not a rewrite.

---

## ADR-003 — Azure-first extraction with **conditional** OpenAI fallback

**Status:** Accepted · 2026-11-07

### Context
Two viable extraction designs:

- **(X) Dual-engine double call**: run Azure Document Intelligence *and* an LLM on every invoice, reconcile both outputs. Maximum redundancy; roughly doubles per-invoice cost and latency, and adds reconciliation complexity on every document — including the ~60–80% where the purpose-built model is already confident.
- **(Y) Azure-primary, conditional LLM fallback**: run `prebuilt-invoice` first; invoke the LLM only when critical-field confidence is below threshold, a critical field is missing, or arithmetic validation fails.

Azure's `prebuilt-invoice` is trained specifically on invoices and returns structured fields, line items, **confidence scores**, and bounding regions — exactly our data contract. General-purpose LLMs are stronger at *reasoning over messy/ambiguous layouts* but weaker guarantees against hallucinated values, and cost/latency per call is higher.

### Decision
Adopt **(Y)**. Stage A = Azure DI `prebuilt-invoice`. A deterministic gate (threshold **0.85** default, configurable; missing critical field; arithmetic mismatch) decides whether Stage B (OpenAI, structured-output JSON schema constrained, DI output + text layer as context) runs. Stage B output always lands in `needs_review` regardless of its self-reported confidence. Full rationale and merge rules: [ARCHITECTURE.md](ARCHITECTURE.md) §6. Raw stage responses persist in `extraction_artifacts` so gate tuning can be re-evaluated offline without re-calling providers.

### Alternatives considered
- **(X) Unconditional double call** — rejected: pays LLM cost+latency on every invoice to hedge against errors that the confidence gate already identifies; also *reduces* data quality signals, since reviewers would face reconciled values without knowing which engine was uncertain.
- **OpenAI-primary** — rejected: DI gives bounding regions natively (required by the review UX) and is the cheaper, purpose-built primary; LLM vision calls per page cost multiples of DI per-page pricing.
- **Cloud-vision alternatives (Google Document AI, AWS Textract)** — credible Stage-A substitutes; Azure chosen for `prebuilt-invoice` field coverage + confidence + region output in one call. The extraction module isolates the provider behind one adapter.

### Consequences
- (+) Blended cost target ≤ $0.02/invoice is achievable; Stage B runs only on the minority (PRD §9.8 caps fallback at ≤ 40% of the golden set).
- (+) p95 latency win: confident invoices skip the slow stage entirely.
- (+) Clean auditability: every field knows its engine (`source_engine`), which is also the training dataset for future tuning.
- (−) Gate mis-tuning risk (too strict → cost creep; too loose → silent errors). Mitigations: threshold is per-deployment config; fallback-rate + auto-pass-rate are first-class KPIs ([PRD](PRD.md) §10); golden-set regression runs on any threshold/model change.
- (−) Two vendor dependencies (only the second one on the hot path when quality drops) — acceptable; both have well-understood outages modes mapped to `503 PROVIDER_OUTAGE`.

---

## ADR-004 — Supabase Auth with FastAPI-side token verification

**Status:** Accepted · 2026-11-07

### Context
We need multi-tenant auth (magic link + password), JWT issuance, and team invites, without building session management. The API is FastAPI; Supabase is already our database host.

### Decision
Use **Supabase Auth** as the identity provider (user store, sessions, JWT issuance), and have **FastAPI verify the HS256 JWT locally** with `SUPABASE_JWT_SECRET` on every request — no network call to Supabase in the hot path. Tenant context comes from the `X-Tenant-Id` header, validated against the `tenant_members` table on each request (role included); the JWT subject alone is never sufficient for tenant-scoped access. The backend performs DB reads/writes with the service role while filtering every query by the resolved tenant; RLS remains enabled as defense-in-depth for direct-client paths ([DATABASE.md](DATABASE.md) §8).

### Alternatives considered
- **Supabase Edge Functions as the whole API** — rejected: no Python runtime (Azure DI / ARQ ecosystem is Python-first), function time limits vs. pipeline needs, and it would splinter the backend across two runtimes.
- **Full custom auth (own user tables + sessions)** — rejected: rebuilding password reset, email verification, invite flows, and secure session handling is pure liability at founding-team scale.
- **Third-party auth (Auth0/Clerk) + Supabase DB** — rejected: two vendors doing adjacent work; Supabase Auth is co-located with the database and priced within it.
- **Verify tokens by calling Supabase's `/user` endpoint per request** — rejected: adds a network hop and its availability to every API call; local HS256 verification has no such coupling (exp/aud checked locally; short token lifetimes bound revocation lag).

### Consequences
- (+) Auth features (magic link, invites, rate limiting on auth endpoints) for near-zero build cost.
- (+) O(1) request auth: signature check + one indexed membership lookup (cacheable per request).
- (−) HS256 means the same secret class verifies and *could mint* tokens — `SUPABASE_JWT_SECRET` is backend-only, never in the frontend bundle (frontend uses the anon key). If we later need asymmetric keys, Supabase supports switching to asymmetric JWT signing keys without contract changes.
- (−) Revocation lag ≈ token lifetime; acceptable for MVP, revisit if tenant removal needs instant cut-off (membership row deletion already kills tenant access immediately — the JWT alone grants nothing).

---

## ADR-005 — Shared-schema multi-tenancy with PostgreSQL RLS

**Status:** Accepted · 2026-11-07

### Context
Tenants are small businesses; count is expected in the thousands, each with modest data. We must guarantee isolation while keeping ops simple on Supabase.

### Decision
Single schema, `tenant_id` column on every tenant-scoped table, composite indexes led by `tenant_id`, plus RLS policies (helper functions `is_tenant_member` / `has_tenant_role`) as defense-in-depth. Full matrix: [DATABASE.md](DATABASE.md) §8.

### Alternatives considered
- **Schema-per-tenant** — rejected: migration fan-out (N schemas × every migration) and connection/catalog bloat at thousands of tenants; overkill for tenant data volumes that live comfortably in one partitioned table set.
- **Database-per-tenant** — rejected: incompatible with the single-Supabase-project model; backup/restore and cross-tenant analytics become operational burdens.

### Consequences
- (+) One migration path, one backup, simple cross-tenant ops analytics for us (not exposed to tenants).
- (−) Noisy-neighbor risk at scale — mitigated when needed by `tenant_id`-led partitioning; not an MVP concern.
- (−) A missing `WHERE tenant_id` in the service role path could leak data — mitigated by repository-layer discipline, RLS underneath, and the cross-tenant test gate (PRD §9.3).

---

## ADR-006 — UUID primary keys

**Status:** Accepted · 2026-11-07

### Context
Keys appear in URLs, CSV exports, audit metadata, and are generated across two processes (API and worker) plus client-visible flows (upload returns the invoice ID immediately).

### Decision
`gen_random_uuid()` for all PKs. (If ID-ordering needs emerge in high-write tables, a migration to UUIDv7 generation is additive and backward-compatible at the type level.)

### Alternatives considered
- **Bigserial** — rejected: exposes business volume, requires central sequence coordination across API/worker, leaks enumerable IDs in exports.
- **Application-generated UUIDv7 now** — attractive (index locality) but adds generation code before there's evidence of write-hotness; revisit per-table if bloat appears.

### Consequences
- (+) Globally unique across processes; safe to expose; merge-friendly.
- (−) Random UUIDv4 locality is worse for index insert perf — irrelevant at MVP volume; ADR notes the UUIDv7 upgrade path.

---

## ADR-007 — `NUMERIC(15,4)` for all monetary values

**Status:** Accepted · 2026-11-07

### Context
FOLEXO stores supplier-invoice money that must reconcile to the penny against exports and, eventually, accounting integrations. Binary floats (IEEE 754) cannot represent decimal fractions exactly (0.1 + 0.2 ≠ 0.3), which is how financial sums silently drift.

### Decision
All monetary columns are `numeric(15,4)` (precision 15, scale 4 — supports values up to 99,999,999,999.9999); quantities `numeric(12,4)`; tax rates `numeric(6,4)` as fractions. Arithmetic validation tolerance is 0.01 in invoice currency. The API serializes money as **strings** ([API.md](API.md) §1.1) so no JSON client can degrade precision via IEEE parsing; [`app/core/money.py`](../backend) centralizes parsing/formatting.

### Alternatives considered
- **`float8`** — rejected outright (drift).
- **Integer minor units (cents)** — considered; rejected: invoice documents carry 2–4 decimal places per line depending on jurisdiction/currency; `NUMERIC` preserves printed precision without a normalization policy we'd have to defend per country.
- **`NUMERIC(12,2)`** — insufficient scale for some B2B unit prices and non-decimal currencies (e.g., 4-decimal pricing).

### Consequences
- (+) Penny-exact arithmetic validation and export reconciliation are achievable by construction.
- (−) Most JSON client languages parse money strings into their own decimal types — a deliberate, documented friction ([API.md](API.md) §1.1).

---

## ADR-008 — Spatial coordinates stored as normalized JSONB polygons

**Status:** Accepted · 2026-11-07

### Context
The review UX requires outlining where each field was found on the source document (FR-4). Azure DI returns pixel/point-based regions tied to page dimensions, which vary per document and per client rendering resolution. A future OCR/engine swap must not force a storage migration.

### Decision
Store per-field bounding polygons in `invoice_fields.bbox` (JSONB) in a canonical, engine-independent shape: coordinates normalized to page dimensions (0–1, origin top-left, clockwise vertices), with `unit`, `page_number`, and the polygon ([ARCHITECTURE.md](ARCHITECTURE.md) §7). Engine-native geometry (page width/height, raw regions) is preserved verbatim inside `extraction_artifacts.payload`, so re-normalization or re-rendering never requires re-calling a provider. `bbox` is nullable — fields with no document location legitimately have none.

### Alternatives considered
- **PostGIS geometry** — rejected: geospatial extension + index machinery for screen-rectangle overlays is heavyweight; no geo-queries are in scope.
- **Raw pixel coordinates** — rejected: couples stored data to the provider's rasterization; breaks under engine change or client zoom levels.
- **Separate `field_coordinates` table** — rejected: 1:1 with `invoice_fields` (only `line_item:<n>` keys add rows); JSONB keeps reads single-table for the review payload.

### Consequences
- (+) Frontend renders `x × rendered_width` directly — resolution-independent, no provider SDK in the client.
- (+) Engine swaps don't touch the storage contract.
- (−) JSONB lacks rigid schema enforcement — polygon shape validation lives in the application layer and is documented as the contract here; acceptable because coordinates are written by one worker path only.
