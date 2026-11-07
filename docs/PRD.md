# FOLEXO — Product Requirements Document (PRD)

| | |
|---|---|
| **Product** | FOLEXO (Invoice AI) |
| **Version** | 1.0 — MVP specification |
| **Status** | Approved for Phase 1 baseline |
| **Last updated** | 2026-11-07 |
| **Owner** | Founding team |

---

## 1. Vision

FOLEXO turns the accounts-payable inbox into a review-in-seconds workflow: **upload supplier invoices, receive structured and verified data, approve, export.** Every extracted field carries a confidence score and a spatial link back to the exact location on the source document, so a human can verify AI output in seconds instead of re-keying the invoice in minutes.

The product bet: AI extraction accuracy is already high enough that the human's job shifts from *data entry* to *verification* — provided the UI makes verification radically fast (confidence flags, bounding-box overlays, keyboard-first corrections).

## 2. Problem statement

Small and mid-sized businesses process supplier invoices manually:

- **Cost**: manual keying and checking runs roughly 5–15 minutes per invoice at meaningful labour cost.
- **Errors**: mistyped amounts and wrong payment terms cause overpayments, missed discounts, and supplier disputes.
- **No trail**: approvals happen over email/chat with no audit log; duplicate invoices slip through.
- **Fragmentation**: the accounting stack (QuickBooks, Xero, ERP) receives data late and second-hand.

Existing AP tools are priced and designed for mid-market+ finance departments. SMB founders and small finance teams are underserved.

## 3. Goals and non-goals

### Goals (MVP)
1. Reduce per-invoice handling time to **under 60 seconds** for clean digital invoices.
2. Achieve **≥ 95% field-level accuracy** on the four critical fields (supplier, invoice number, date, total) for digital PDFs.
3. Provide **complete tenant isolation** — verified by automated cross-tenant tests.
4. Ship a **full audit trail** of uploads, extractions, corrections, approvals, and exports.
5. Keep the extraction pipeline **cost-efficient**: the expensive LLM stage runs only when the primary engine is uncertain.

### Non-goals (explicitly out of scope for MVP)
- Payments, payment scheduling, or any money movement.
- Purchase-order (PO) matching and 3-way match.
- Direct ERP/accounting integrations (QuickBooks/Xero/DATEV) — CSV export only in MVP.
- Automated email intake (invoices@company parsing) — manual upload only.
- Accounts Receivable features (issuing invoices) — FOLEXO MVP is AP-only.
- E-invoicing compliance formats (ZUGFeRD, Factur-X, Peppol).
- Non-Latin script OCR; multi-currency conversion/FX.
- Mobile native apps (responsive web only).

## 4. User personas

### P1 — Priya, the AP Clerk (primary user)
- Processes 30–80 supplier invoices per day for one company.
- Fluent in the supplier directory; knows immediately when an amount "looks wrong".
- **Needs**: a single ordered review queue, low-confidence fields flagged before she reads anything, bounding-box highlighting that jumps to the field on the document, keyboard-only correction flow, duplicate warnings.
- **Pain today**: retyping every invoice; alt-tabbing between PDF viewer and spreadsheet.

### P2 — Marcus, the Finance Manager / Controller (approver)
- Owns cash-out: approves what Priya processes, audits history at month-end.
- **Needs**: approval queue with totals and supplier context, rejection with reason, export-to-CSV that reconciles to the penny, immutable audit trail, dashboards (counts and totals by status/month).
- **Pain today**: no visibility between "Priya typed it" and "we paid it".

### P3 — Sofia, the Founder / Tenant Admin (buyer & administrator)
- Set up FOLEXO for her 12-person company; not technical.
- **Needs**: self-serve workspace creation, invite teammates by email, role assignment, see usage and pipeline health, confidence that her data never leaks to another tenant.
- **Pain today**: considering hiring a bookkeeper just for invoices.

## 5. MVP scope

### 5.1 In scope

| Capability | Detail |
|---|---|
| Document upload | Drag-and-drop or file picker; PDF, PNG, JPEG, TIFF; ≤ 25 MB; ≤ 100 pages; per-tenant dedup by content hash |
| Async extraction | Header fields + line items with per-field confidence, engine attribution, and spatial coordinates; Azure DI primary, conditional OpenAI fallback |
| Supplier directory | Auto-created from extraction, editable; dedup by normalized name / tax ID |
| Review & correction | Field-level editing; corrections recorded (before/after, actor, timestamp); arithmetic re-validation on save |
| Approval workflow | `needs_review → ready → approved/rejected`; reject requires reason; reprocess action re-runs extraction |
| Export | Filtered CSV export (approved or all statuses) with totals re-validation |
| Multi-tenancy | Shared-schema tenancy with RLS; roles: owner, admin, member, viewer; invite by email |
| Audit log | Tenant-scoped, append-only; actor + action + entity + metadata |
| Dashboard | Counts and monetary totals by status; current-month processed volume; extraction auto-pass rate |

### 5.2 Out of scope
See §3 Non-goals. Anything not listed in §5.1 requires a PRD change record before implementation.

## 6. Key flows

### Flow A — Upload → extraction (happy path)
1. Priya drops `acme-inv-1042.pdf` on the queue. Upload returns `202 Accepted` immediately; the invoice appears in the queue as **uploaded**, then **processing**.
2. Worker runs Stage A (Azure DI `prebuilt-invoice`). All critical fields ≥ confidence threshold and arithmetic validates → status **ready**, no human touch.
3. Invoice lands in "Ready" with green confidence chips.

### Flow B — Review with fallback (primary path for scans)
1. A photographed invoice extracts with `total_amount` confidence 0.58 → Stage B (OpenAI fallback) reconciles against the OCR text → status **needs_review**.
2. Priya opens the document view; the total field is outlined in amber on-page. She confirms or corrects; saving re-validates arithmetic.
3. Status → **ready**; correction is audited.

### Flow C — Approve → export
1. Marcus filters the queue to **ready**, reviews totals, clicks approve (or rejects with a reason).
2. At month-end he exports approved invoices to CSV; row totals re-validate against line items on export.

## 7. Functional requirements

| ID | Requirement |
|---|---|
| FR-1 | The system SHALL accept uploads of PDF, PNG, JPEG, TIFF ≤ 25 MB and ≤ 100 pages, authenticated and tenant-scoped. |
| FR-2 | The system SHALL reject exact-duplicate files within a tenant (SHA-256 content hash) with a warning referencing the original. |
| FR-3 | The system SHALL extract, per invoice: supplier name, invoice number, invoice date, due date, currency, subtotal, tax, total, PO number, and line items (description, quantity, unit price, line total). |
| FR-4 | Every machine-extracted field SHALL store: raw text as printed, normalized value, confidence (0–1), source engine, page number, and a bounding polygon in normalized page coordinates. |
| FR-5 | When any critical field's confidence is below the configured threshold, or arithmetic validation fails, the system SHALL run the fallback LLM stage before marking the invoice for review. |
| FR-6 | The system SHALL classify each processed invoice as `ready` (auto-pass) or `needs_review` based on confidence and validation outcomes. |
| FR-7 | Reviewers SHALL be able to correct any header field or line item; corrections SHALL re-run arithmetic validation and SHALL be recorded in the audit log. |
| FR-8 | Users with `owner`/`admin`/`member` roles SHALL be able to approve/reject; rejection SHALL require a reason; `viewer` role SHALL be read-only. |
| FR-9 | The system SHALL support reprocessing an invoice; re-extraction SHALL NOT overwrite manually corrected fields. |
| FR-10 | The system SHALL auto-create or match a supplier record per invoice (match by normalized name or tax ID within the tenant). |
| FR-11 | The system SHALL provide filtered CSV export with totals re-validated at export time. |
| FR-12 | The system SHALL enforce tenant isolation at both the API layer and the database (RLS), verified by automated cross-tenant tests. |
| FR-13 | The system SHALL record an audit entry for: upload, extraction stage completion, correction, approval, rejection, reprocess, export, membership change. |
| FR-14 | The system SHALL expose processing state per invoice (status + job record) queryable via API so the frontend can poll progress. |

## 8. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | Extraction latency: p95 ≤ 60 s for digital PDFs, ≤ 120 s for image uploads, end-to-end upload→status. |
| NFR-2 | API availability ≥ 99.5% monthly; extraction queue is allowed to degrade independently of the API. |
| NFR-3 | All monetary values stored as `NUMERIC(15,4)`; never floating point; serialized as strings in the API. |
| NFR-4 | Secrets only via environment variables; service-role keys never shipped to the frontend. |
| NFR-5 | Uploads ≤ 2 s p95 for a 5 MB file; list endpoints ≤ 500 ms p95 at 100k invoices per tenant. |
| NFR-6 | GDPR posture: tenant-initiated hard delete of an invoice removes document + derived rows; audit entries retain only non-personal metadata. |
| NFR-7 | Structured JSON logs with request IDs; jobs emit stage timings for extraction-quality dashboards. |

## 9. Acceptance criteria (MVP exit)

MVP is accepted when **all** of the following hold:

1. **Accuracy** — On a 200-invoice golden set (120 digital PDFs, 80 photos/scans): ≥ 95% exact-field accuracy on supplier name, invoice number, invoice date, and total for digital PDFs; ≥ 90% for photos/scans. Measured by the Phase 5 evaluation harness.
2. **Latency** — NFR-1 met over a 500-invoice mixed-load run.
3. **Isolation** — Automated test suite proves: cross-tenant API access returns 403/404, and RLS denies direct-query cross-tenant reads for all tenant-scoped tables.
4. **Review UX** — A reviewer can resolve a `needs_review` invoice in ≤ 60 s median, including opening the document with bounding-box overlays and saving ≥ 1 correction (validated in usability pass with 3 test users).
5. **Approval & audit** — Approve/reject actions produce correct status transitions, required rejection reasons, and complete audit entries.
6. **Export** — A 1,000-invoice export matches database totals exactly (penny-perfect) with arithmetic re-validation passing on every row.
7. **API contract** — All v1 endpoints implemented per `docs/API.md`; OpenAPI schema published; contract tests green.
8. **Fallback economics** — Fallback stage triggers on ≤ 40% of the golden set and never runs when all critical fields pass threshold + arithmetic (verifiable from `processing_jobs` / `extraction_artifacts`).

## 10. Success metrics (post-launch KPIs)

| Metric | Target |
|---|---|
| Auto-pass rate (digital PDFs, no human edit) | ≥ 60% by month 3 |
| Median review time per `needs_review` invoice | ≤ 45 s |
| Extraction cost per invoice | ≤ $0.02 blended |
| Week-4 retention (tenants uploading ≥ 10 invoices/week) | ≥ 50% |

## 11. Risks & open questions

| Risk / question | Mitigation |
|---|---|
| Azure DI accuracy on low-quality phone photos may force high fallback rates (cost) | Threshold is configurable per deployment; monitor fallback rate KPI; consider pre-upload image normalization in Phase 5 |
| LLM fallback can hallucinate plausible-but-wrong values | Fallback output constrained to JSON schema over DI fields + OCR text; every fallback field stays flagged `needs_review` regardless of self-reported confidence |
| Duplicate numbering across credit notes vs. real duplicates | MVP warns (flags `is_possible_duplicate`) rather than blocking; credit-note workflow deferred |
| VAT/tax model differences across countries | MVP stores tax amount only; tax-rate breakdown per line deferred to post-MVP |
| Multi-currency invoices | MVP stores currency code per invoice; no FX conversion |

## 12. Release mapping

MVP scope maps to delivery phases 1–8 in [`PROJECT_STATUS.md`](PROJECT_STATUS.md). Post-MVP backlog (integrations, PO matching, email intake) is intentionally not specified here to keep MVP boundaries hard.
