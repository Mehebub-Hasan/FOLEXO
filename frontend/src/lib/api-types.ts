/**
 * Types mirroring the backend API contract (docs/API.md + Phase 3 endpoints).
 * Monetary values arrive as strings (fixed-point decimals) — never parse them
 * into floats.
 */

export type InvoiceStatus =
  | "uploaded"
  | "processing"
  | "needs_review"
  | "ready"
  | "approved"
  | "rejected"
  | "exported"
  | "archived"
  | "failed";

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
