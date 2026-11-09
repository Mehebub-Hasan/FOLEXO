/**
 * Types mirroring the backend API contract (docs/API.md + Phase 3 endpoints).
 * Monetary values arrive as strings (fixed-point decimals) — never parse them
 * into floats.
 */

export type InvoiceStatus =
  | "uploaded"
  | "queued"
  | "processing"
  | "needs_review"
  | "ready"
  | "approved"
  | "rejected"
  | "exported"
  | "archived"
  | "failed";

export interface ValidationIssue {
  code: string;
  field: string;
  message: string;
  severity: "ERROR" | "WARNING";
  details?: Record<string, unknown> | null;
}

/** Normalized 0–1 polygon, docs/ARCHITECTURE.md §7 shape. */
export interface BoundingBox {
  unit: "normalized";
  page_number: number;
  polygon: number[][];
}

export interface Vendor {
  id: string;
  name: string;
  tax_id: string | null;
  email: string | null;
  default_currency: string | null;
}

export interface VendorDirectoryEntry {
  id: string;
  name: string;
  normalized_name: string;
  email: string | null;
  address: string | null;
  tax_identifier: string | null;
  default_currency: string | null;
  invoice_count: number;
  total_spend: string | null;
  last_invoice_date: string | null;
}

export interface VendorInvoiceRow {
  id: string;
  status: InvoiceStatus;
  invoice_number: string | null;
  invoice_date: string | null;
  total_amount: string | null;
  currency_code: string | null;
  created_at: string;
}

export interface VendorDetailResponse {
  id: string;
  name: string;
  normalized_name: string;
  email: string | null;
  address: string | null;
  tax_identifier: string | null;
  notes: string | null;
  default_currency: string | null;
  invoice_count: number;
  total_spend: string | null;
  last_invoice_date: string | null;
  invoices: VendorInvoiceRow[];
  pagination: { total: number; limit: number; offset: number };
}

export interface InvoiceLineItem {
  id: string;
  line_number: number;
  description: string | null;
  quantity: string | null;
  unit_price: string | null;
  tax_rate: string | null;
  line_total: string | null;
  page_number: number | null;
  confidence: string | null;
  bbox: BoundingBox | null;
  source_engine: string | null;
  is_corrected: boolean;
}

export interface InvoiceRecord {
  id: string;
  organization_id: string;
  document_id: string;
  vendor_id: string | null;
  status: InvoiceStatus;
  invoice_number: string | null;
  invoice_date: string | null;
  due_date: string | null;
  currency_code: string | null;
  subtotal_amount: string | null;
  tax_amount: string | null;
  total_amount: string | null;
  extraction_engine: string | null;
  confidence_min: string | null;
  confidence_avg: string | null;
  is_possible_duplicate: boolean;
  validation_errors: ValidationIssue[] | null;
  approved_by_id: string | null;
  approved_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface InvoiceDetail {
  invoice: InvoiceRecord;
  vendor: Vendor | null;
  document: {
    id: string;
    storage_path: string;
    original_filename: string;
    mime_type: string;
    file_size_bytes: number;
    content_hash: string;
    created_at: string;
  };
  line_items: InvoiceLineItem[];
  file_url: string;
  /** Core-field boxes from the latest extraction, keyed by DI field name. */
  field_boxes: Record<string, BoundingBox> | null;
}

export interface PresignResponse {
  storage_path: string;
  upload_url: string;
  is_duplicate: boolean;
  existing_invoice_id: string | null;
}

export interface InvoiceDocument {
  id: string;
  storage_path: string;
  original_filename: string;
  mime_type: string;
  file_size_bytes: number;
  content_hash: string;
  created_at: string;
}

export interface InvoiceCreated {
  id: string;
  organization_id: string;
  document_id: string;
  vendor_id: string | null;
  status: InvoiceStatus;
  created_at: string;
  updated_at: string;
}

export interface InvoiceCreateResponse {
  invoice: InvoiceCreated;
  document: InvoiceDocument;
}

export interface InvoiceListItem {
  id: string;
  status: InvoiceStatus;
  original_filename: string;
  vendor_name: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  total_amount: string | null;
  currency_code: string | null;
  created_at: string;
}

export interface Paginated<T> {
  data: T[];
  pagination: {
    next_cursor: string | null;
    has_more: boolean;
  };
}
