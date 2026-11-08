"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { InvoiceDetail, Vendor } from "@/lib/api-types";

export interface PatchLineItemInput {
  description: string | null;
  quantity: string | null;
  unit_price: string | null;
  line_total: string | null;
}

export interface PatchInvoiceInput {
  vendor_id?: string | null;
  invoice_number?: string | null;
  invoice_date?: string | null;
  due_date?: string | null;
  currency?: string | null;
  subtotal?: string | null;
  tax_amount?: string | null;
  total_amount?: string | null;
  line_items?: PatchLineItemInput[];
}

function invalidateInvoiceCaches(client: ReturnType<typeof useQueryClient>, invoiceId: string | null) {
  client.invalidateQueries({ queryKey: ["invoice-detail", invoiceId] });
  client.invalidateQueries({ queryKey: ["invoice-status", invoiceId] });
  client.invalidateQueries({ queryKey: ["invoices"] });
}

/** Save review corrections (PATCH). */
export function useSaveInvoice(invoiceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: PatchInvoiceInput): Promise<InvoiceDetail> => {
      const response = await api.patch<InvoiceDetail>(`/api/v1/invoices/${invoiceId}`, input);
      return response.data;
    },
    onSuccess: (data) => {
      if (invoiceId) invalidateInvoiceCaches(queryClient, invoiceId);
      return data;
    },
  });
}

/** Approve the invoice (needs_review/ready → approved). */
export function useApproveInvoice(invoiceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<InvoiceDetail> => {
      const response = await api.post<InvoiceDetail>(`/api/v1/invoices/${invoiceId}/approve`);
      return response.data;
    },
    onSuccess: () => {
      if (invoiceId) invalidateInvoiceCaches(queryClient, invoiceId);
    },
  });
}

/** Re-run the extraction pipeline (→ queued, then processed by the worker). */
export function useReprocessInvoice(invoiceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<InvoiceDetail> => {
      const response = await api.post<InvoiceDetail>(`/api/v1/invoices/${invoiceId}/reprocess`);
      return response.data;
    },
    onSuccess: () => {
      if (invoiceId) invalidateInvoiceCaches(queryClient, invoiceId);
    },
  });
}

/** Vendor directory for the selector (name-substring search). */
export function useVendors(enabled: boolean) {
  return useQuery({
    queryKey: ["vendors"],
    enabled,
    queryFn: async (): Promise<Vendor[]> => {
      const response = await api.get<Vendor[]>("/api/v1/vendors");
      return response.data;
    },
    staleTime: 60_000,
  });
}

/** Quick-create a vendor from the review page. */
export function useCreateVendor() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { name: string; tax_id?: string | null; email?: string | null }): Promise<Vendor> => {
      const response = await api.post<Vendor>("/api/v1/vendors", input);
      return response.data;
    },
    onSuccess: (vendor) => {
      queryClient.invalidateQueries({ queryKey: ["vendors"] });
      return vendor;
    },
  });
}
