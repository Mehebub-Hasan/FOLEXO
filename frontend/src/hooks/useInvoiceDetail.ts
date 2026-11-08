"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { InvoiceDetail } from "@/lib/api-types";

/** Full review payload for one invoice (header, vendor, items, signed URL). */
export function useInvoiceDetail(invoiceId: string | null) {
  return useQuery({
    queryKey: ["invoice-detail", invoiceId],
    enabled: Boolean(invoiceId),
    queryFn: async (): Promise<InvoiceDetail> => {
      const response = await api.get<InvoiceDetail>(`/api/v1/invoices/${invoiceId}`);
      return response.data;
    },
    retry: 1,
  });
}
