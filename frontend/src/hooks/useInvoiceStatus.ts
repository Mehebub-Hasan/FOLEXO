"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { InvoiceStatus } from "@/lib/api-types";

export interface InvoiceStatusResponse {
  invoice_id: string;
  status: InvoiceStatus;
  progress_percent: number;
  error_message: string | null;
}

const PENDING_STATUSES = new Set<string>(["uploaded", "queued", "processing"]);
const POLL_INTERVAL_MS = 2000;

/**
 * Polls GET /api/v1/invoices/{id}/status every 2 s while the invoice is in a
 * pending pipeline state (uploaded / processing) and stops automatically once
 * a terminal status (needs_review, failed, ready, approved, …) is reached.
 */
export function useInvoiceStatus(invoiceId: string | null) {
  return useQuery({
    queryKey: ["invoice-status", invoiceId],
    enabled: Boolean(invoiceId),
    queryFn: async (): Promise<InvoiceStatusResponse> => {
      const response = await api.get<InvoiceStatusResponse>(
        `/api/v1/invoices/${invoiceId}/status`
      );
      return response.data;
    },
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status !== undefined && PENDING_STATUSES.has(status) ? POLL_INTERVAL_MS : false;
    },
    retry: 1,
  });
}
