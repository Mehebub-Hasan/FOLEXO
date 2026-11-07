# FOLEXO — REST API Contract (v1)

| | |
|---|---|
| **Version** | v1 (contract of record) |
| **Status** | Approved for Phase 1 baseline |
| **Base path** | `{APP_BASE_URL}/api/v1` |
| **Last updated** | 2026-11-07 |
| **Related** | [ARCHITECTURE](ARCHITECTURE.md) §5 (status machine) · [DATABASE](DATABASE.md) · [PRD](PRD.md) |

The contract is the source of truth; FastAPI's generated OpenAPI schema (`/docs`, `/openapi.json`) is a projection of it. Drift checks are a Phase 8 gate. Breaking changes require a `/api/v2` — v1 is additive-only.

---

## 1. Conventions

### 1.1 Versioning & transport
- All business endpoints live under `/api/v1`. `GET /health` is unversioned.
- `Content-Type: application/json` except the multipart upload endpoint (§5.5) and the CSV export (§5.11).
- Timestamps: ISO 8601 UTC (`2026-11-07T14:32:11.284Z`).
- Monetary values: **strings** encoding fixed-point decimals (`"1488.0000"`) — never JSON numbers (NFR-3).
- Unknown request fields are rejected with `422` (strict schemas catch client typos).

### 1.2 Authentication
```
Authorization: Bearer <supabase-access-token>
X-Tenant-Id: <tenant uuid>        # required on every tenant-scoped endpoint
```
- The JWT is issued by Supabase Auth and verified locally by the API (ADR-004).
- `X-Tenant-Id` is accepted only if the JWT subject has a `tenant_members` row for it; otherwise `403 FORBIDDEN_TENANT`. Roles from the membership row determine write permissions (DATABASE §8.3).
- Missing/invalid token → `401`; valid token but no membership → `403`.

### 1.3 Idempotency & correlation
- `POST /invoices` accepts an optional `Idempotency-Key` header; replays of the same key + same content hash within 24 h return the original `202` response without a duplicate upload.
- Every response carries `X-Request-Id` (echoed if provided); include it in error reports.

### 1.4 Rate limits
| Scope | Limit | Exceeded |
|---|---|---|
| Uploads (`POST /invoices`) | 60 / minute / user | `429` + `Retry-After` (seconds) |
| All other endpoints | 120 / minute / user | `429` + `Retry-After` |

### 1.5 Pagination
Cursor-based (stable under concurrent inserts):
- Request: `?limit=25` (1–100, default 25) + `?cursor=<opaque>`
- Response envelope: `{"data": [...], "pagination": {"next_cursor": "…", "has_more": true}}`
- `next_cursor` is `null` on the last page; opaque to clients; sorts default to `created_at desc, id desc` unless documented per endpoint.

### 1.6 Filtering & sorting (list endpoints)
- Dates: inclusive ISO ranges `?created_from=2026-11-01&created_to=2026-11-07`
- Multi-value: comma-separated `?status=needs_review,ready`
- Sorting: `?sort=total_amount&order=desc` on whitelisted fields per endpoint.

## 2. Resource schemas

### 2.1 Invoice
```json
{
  "id": "9f1c2a34-6b7e-4d21-a0f8-1f2e3d4c5b6a",
  "tenant_id": "0d3b9f2e-...",
  "status": "needs_review",
  "supplier": { "id": "c4...", "name": "Acme GmbH" },
  "invoice_number": "INV-2026-0142",
  "invoice_date": "2026-10-28",
  "due_date": "2026-11-27",
  "currency_code": "EUR",
  "po_number": "PO-8841",
  "subtotal_amount": "1240.0000",
  "tax_amount": "248.0000",
  "total_amount": "1488.0000",
  "extraction": {
    "engine": "openai_fallback",
    "confidence_min": 0.620,
    "confidence_avg": 0.913,
    "is_possible_duplicate": false,
    "started_at": "2026-11-07T14:32:12.110Z",
    "finished_at": "2026-11-07T14:32:39.502Z"
  },
  "file": {
    "filename": "acme-inv-1042.pdf",
    "mime_type": "application/pdf",
    "size_bytes": 184322,
    "page_count": 2
  },
  "rejection_reason": null,
  "uploaded_by": "76...",
  "created_at": "2026-11-07T14:32:11.284Z",
  "updated_at": "2026-11-07T14:32:39.610Z"
}
```
`supplier`, `invoice_number`, amounts etc. are `null` while `status ∈ {uploaded, processing}` or after a `failed` extraction.

### 2.2 Extracted field
```json
{
  "field_key": "total_amount",
  "raw_text": "1.488,00",
  "value_text": "1488.00",
  "value_number": "1488.0000",
  "confidence": 0.620,
  "source_engine": "azure_di",
  "page_number": 1,
  "bbox": {
    "unit": "normalized",
    "page_number": 1,
    "polygon": [[0.612, 0.901], [0.874, 0.901], [0.874, 0.943], [0.612, 0.943]]
  },
  "is_corrected": false,
  "corrected_by": null,
  "updated_at": "2026-11-07T14:32:39.588Z"
}
```
`bbox` is `null` when the field has no document location (manual additions, matched metadata). Polygon contract: [ARCHITECTURE.md](ARCHITECTURE.md) §7.

### 2.3 Supplier
```json
{
  "id": "c4...",
  "name": "Acme GmbH",
  "tax_id": "DE812345678",
  "email": "accounts@acme.example",
  "address_line1": "Hauptstr. 1",
  "city": "Berlin",
  "postal_code": "10115",
  "country_code": "DE",
  "default_currency": "EUR",
  "payment_terms_days": 30,
  "invoice_count": 14,
  "created_at": "2026-10-02T09:14:00.000Z",
  "updated_at": "2026-11-05T16:02:31.000Z"
}
```

### 2.4 Processing job
```json
{
  "id": "1a...",
  "invoice_id": "9f...",
  "job_type": "extract_invoice",
  "job_status": "running",
  "attempt": 1,
  "max_attempts": 3,
  "engine_used": null,
  "error_code": null,
  "error_message": null,
  "duration_ms": null,
  "queued_at": "2026-11-07T14:32:11.300Z",
  "started_at": "2026-11-07T14:32:12.105Z",
  "finished_at": null
}
```

## 3. Error model

Every non-2xx response uses one envelope (no HTML error pages, ever):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request failed validation.",
    "details": [
      { "field": "due_date", "issue": "must be a valid ISO 8601 date (YYYY-MM-DD)" }
    ],
    "request_id": "01JD7W3Z..."
  }
}
```

| HTTP | `code` | When |
|---|---|---|
| 400 | `BAD_REQUEST` | Malformed request (unparseable body, bad multipart) |
| 401 | `UNAUTHENTICATED` | Missing/expired/invalid JWT |
| 403 | `FORBIDDEN_TENANT` | No membership for `X-Tenant-Id` |
| 403 | `FORBIDDEN_ROLE` | Role insufficient for the action (e.g., viewer approving) |
| 404 | `NOT_FOUND` | Resource does not exist in the caller's tenant (cross-tenant IDs are indistinguishable from missing ones) |
| 409 | `DUPLICATE_FILE` | `content_hash` already exists in tenant; `details[].existing_invoice_id` provided |
| 409 | `CONFLICT_STATE` | Illegal status transition (§6) or concurrent modification |
| 413 | `FILE_TOO_LARGE` | > `MAX_UPLOAD_SIZE_MB` |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | Not PDF/PNG/JPEG/TIFF |
| 422 | `VALIDATION_ERROR` | Schema validation failure; `details[]` names fields |
| 422 | `VALIDATION_ERROR` (arithmetic) | Correction batch breaks arithmetic: `details[].code = ARITHMETIC_MISMATCH` |
| 429 | `RATE_LIMITED` | Rate limit exceeded |
| 500 | `INTERNAL_ERROR` | Unhandled server fault (message stays generic; `request_id` for triage) |
| 503 | `PROVIDER_OUTAGE` | Azure DI / OpenAI unreachable and no cached result applies |

## 4. Endpoint index

| Method | Path | Auth | Min role | Purpose |
|---|---|---|---|---|
| GET | `/health` | — | — | Liveness (unversioned) |
| GET | `/api/v1/me` | JWT | — | Profile + tenant memberships |
| GET | `/api/v1/tenants` | JWT | — | Tenants the caller belongs to |
| POST | `/api/v1/tenants` | JWT | — | Create tenant (caller becomes `owner`) |
| GET | `/api/v1/tenants/{tenant_id}` | JWT | viewer | Tenant detail |
| PATCH | `/api/v1/tenants/{tenant_id}` | JWT | admin | Update tenant settings |
| GET | `/api/v1/tenants/{tenant_id}/members` | JWT | viewer | List members |
| POST | `/api/v1/tenants/{tenant_id}/members` | JWT | admin | Invite member |
| PATCH | `/api/v1/tenants/{tenant_id}/members/{user_id}` | JWT | admin | Change role |
| DELETE | `/api/v1/tenants/{tenant_id}/members/{user_id}` | JWT | admin | Remove member |
| GET | `/api/v1/suppliers` | JWT | viewer | List suppliers (search/paginated) |
| POST | `/api/v1/suppliers` | JWT | member | Create supplier |
| GET | `/api/v1/suppliers/{supplier_id}` | JWT | viewer | Supplier detail |
| PATCH | `/api/v1/suppliers/{supplier_id}` | JWT | member | Update supplier |
| DELETE | `/api/v1/suppliers/{supplier_id}` | JWT | admin | Delete supplier (invoices keep `supplier_id = null`) |
| POST | `/api/v1/invoices` | JWT | member | Upload document → 202 |
| GET | `/api/v1/invoices` | JWT | viewer | List/filter invoices |
| GET | `/api/v1/invoices/{invoice_id}` | JWT | viewer | Invoice detail (poll target) |
| PATCH | `/api/v1/invoices/{invoice_id}` | JWT | member | Review corrections (header + line items) |
| DELETE | `/api/v1/invoices/{invoice_id}` | JWT | admin | Hard delete (GDPR) |
| GET | `/api/v1/invoices/{invoice_id}/fields` | JWT | viewer | Extracted fields + coordinates |
| GET | `/api/v1/invoices/{invoice_id}/line-items` | JWT | viewer | Line items |
| GET | `/api/v1/invoices/{invoice_id}/jobs` | JWT | viewer | Processing job history |
| GET | `/api/v1/invoices/{invoice_id}/file` | JWT | viewer | Short-lived signed URL |
| POST | `/api/v1/invoices/{invoice_id}/reprocess` | JWT | member | Re-run extraction |
| POST | `/api/v1/invoices/{invoice_id}/approve` | JWT | member | Approve |
| POST | `/api/v1/invoices/{invoice_id}/reject` | JWT | member | Reject (reason required) |
| GET | `/api/v1/invoices/export.csv` | JWT | viewer | Synchronous CSV export (≤ 5,000 rows) |
| GET | `/api/v1/stats/summary` | JWT | viewer | Dashboard KPIs |

`{tenant_id}` is taken from `X-Tenant-Id` for tenant-scoped resources; path tenant IDs exist only on the tenant-management endpoints and must match a membership.

## 5. Endpoint reference

### 5.1 `GET /health` — unversioned
`200` → `{"status": "ok", "version": "…", "checks": {"db": "ok", "redis": "ok"}}` (checks filled from Phase 3/5 onward; degraded dependencies return `503` with the failing check named).

### 5.2 `GET /api/v1/me`
```json
{
  "profile": { "id": "76...", "email": "priya@example.com", "full_name": "Priya S." },
  "memberships": [
    { "tenant_id": "0d...", "tenant_name": "Acme Ltd", "tenant_slug": "acme-ltd", "role": "member" }
  ]
}
```

### 5.3 `POST /api/v1/tenants`
Request `{"name": "Acme Ltd", "country_code": "GB", "default_currency": "GBP"}`
→ `201` with tenant object; the creator is enrolled as `owner` automatically.

### 5.4 `POST /api/v1/tenants/{tenant_id}/members`
Request `{"email": "marcus@example.com", "role": "member"}` → `201` with membership row (`pending` invite semantics handled by Supabase Auth in MVP — the invitee joins by accepting an auth invite).

### 5.5 `POST /api/v1/invoices` — upload
- Request: `multipart/form-data` with `file` (required) and optional `supplier_id`.
- Constraints: mime ∈ {`application/pdf`, `image/png`, `image/jpeg`, `image/tiff`}, ≤ `MAX_UPLOAD_SIZE_MB`, ≤ `MAX_UPLOAD_PAGES` (page check happens during processing, not upload).
- Side effects: Storage write, `invoices` row (`status=uploaded`), `processing_jobs` row, job enqueue, audit entry.
- `202 Accepted`:
```json
{
  "data": {
    "invoice": { "id": "9f...", "status": "uploaded", "...": "…" },
    "job": { "id": "1a...", "job_status": "queued" }
  }
}
```
- Errors: `400` (bad multipart), `409 DUPLICATE_FILE`, `413`, `415`, `422`.

### 5.6 `GET /api/v1/invoices`
- Filters: `status` (multi), `supplier_id`, `created_from`/`created_to`, `invoice_date_from`/`invoice_date_to`, `q` (matches invoice number, PO number, supplier name), `possible_duplicates_only=true`.
- Sort whitelist: `created_at` (default, desc), `invoice_date`, `total_amount`, `due_date`.
- → `200` paginated envelope of invoice objects (§2.1).

### 5.7 `GET /api/v1/invoices/{invoice_id}/file`
→ `200` `{"url": "https://…<signed>", "expires_at": "2026-11-07T14:40:00Z"}`. Signed URL TTL ≤ 300 s; the API never proxies file bytes.

### 5.8 `PATCH /api/v1/invoices/{invoice_id}` — review corrections
Allowed from `needs_review` (and `ready`, which stays `ready` if validation passes). Request:

```json
{
  "header": {
    "supplier_id": "c4...",
    "invoice_number": "INV-2026-0142",
    "invoice_date": "2026-10-28",
    "due_date": "2026-11-27",
    "currency_code": "EUR",
    "po_number": "PO-8841",
    "subtotal_amount": "1240.0000",
    "tax_amount": "248.0000",
    "total_amount": "1488.0000"
  },
  "line_items": [
    {
      "line_number": 1,
      "description": "Consulting services October",
      "quantity": "10.0000",
      "unit_price": "100.0000",
      "tax_rate": "0.2000",
      "line_total": "1000.0000"
    }
  ]
}
```

Rules:
- Partial updates allowed: omitted fields keep current values.
- Every changed value is recorded as a correction (audit: before/after/actor; `invoice_fields.is_corrected = true` for header fields; line items set `is_corrected`).
- Arithmetic validation runs server-side on the **merged result** (`|Σ line_totals − subtotal| ≤ 0.01` and `|subtotal + tax − total| ≤ 0.01`, invoice currency). Failure → `422 ARITHMETIC_MISMATCH`, nothing persisted.
- Success from `needs_review` → status becomes `ready`; `200` returns the updated invoice.

### 5.9 Action endpoints
- `POST …/approve` — allowed from `needs_review`, `ready` → `approved`. Empty body, `200` invoice.
- `POST …/reject` — allowed from `needs_review`, `ready` → `rejected`. Body `{"reason": "…"}` (1–500 chars, required); missing → `422`.
- `POST …/reprocess` — allowed from `failed`, `needs_review`, `ready` → status `processing`, new `processing_jobs` row, re-enqueue. Manual corrections are preserved (worker overwrite rule, ARCHITECTURE §5.2). `202` with invoice + job.
- Illegal source status → `409 CONFLICT_STATE` with `details[].allowed_from` listing valid states.

### 5.10 `DELETE /api/v1/invoices/{invoice_id}`
Hard delete per GDPR posture (DATABASE §9): document object + derived rows removed; audit metadata retained. `204`. Not allowed from `exported` (financial record) → `409`.

### 5.11 `GET /api/v1/invoices/export.csv`
- Filters: `status` (default `approved`), `invoice_date_from`/`to`, `supplier_id`.
- Sync streaming response: `text/csv`, columns `invoice_id,invoice_number,invoice_date,due_date,supplier_name,currency,subtotal,tax,total,status,approved_at`; totals re-validated per row on export (penny-perfect, PRD §9.6). > 5,000 rows → `400 BAD_REQUEST` ("narrow the filter"; async export is post-MVP).

### 5.12 `GET /api/v1/stats/summary`
```json
{
  "by_status": { "needs_review": 4, "ready": 12, "approved": 137 },
  "month": { "processed": 153, "total_value": "89420.5500", "currency": "EUR" },
  "extraction": { "auto_pass_rate": 0.71, "fallback_rate": 0.22 }
}
```
(All monetary aggregates in the tenant's `default_currency`; mixed-currency tenants get per-currency breakdowns post-MVP.)

## 6. Status transition map (server-enforced)

| From | Action | To |
|---|---|---|
| `uploaded` | worker pickup | `processing` |
| `processing` | validation passed | `ready` |
| `processing` | validation failed / fallback flagged | `needs_review` |
| `processing` | terminal job failure | `failed` |
| `needs_review` | `PATCH` passing validation | `ready` |
| `needs_review` / `ready` | `approve` | `approved` |
| `needs_review` / `ready` | `reject` | `rejected` |
| `failed` / `needs_review` / `ready` | `reprocess` | `processing` |
| `approved` | export recorded | `exported` |
| `exported` / `rejected` | archive (post-MVP lifecycle job) | `archived` |

Anything else → `409 CONFLICT_STATE`.

## 7. CORS & browser rules
- Allowed origins: `ALLOWED_ORIGINS` (env). Headers allowed: `Authorization`, `Content-Type`, `X-Tenant-Id`, `Idempotency-Key`. Exposed: `X-Request-Id`, `Retry-After`.
- Credentials mode: not used (bearer tokens only).
