"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { VendorDetailResponse, VendorDirectoryEntry } from "@/lib/api-types";

/** Directory rows with aggregated metrics, filtered by the search term. */
export function useVendorsDirectory(search: string) {
  return useQuery({
    queryKey: ["vendors-directory", search],
    queryFn: async (): Promise<VendorDirectoryEntry[]> => {
      const response = await api.get<VendorDirectoryEntry[]>("/api/v1/vendors", {
        params: search ? { search } : {},
      });
      return response.data;
    },
    placeholderData: (previous) => previous,
  });
}

/** Vendor details + paginated invoice history. */
export function useVendorDetail(vendorId: string | null, offset = 0) {
  return useQuery({
    queryKey: ["vendor-detail", vendorId, offset],
    enabled: Boolean(vendorId),
    queryFn: async (): Promise<VendorDetailResponse> => {
      const response = await api.get<VendorDetailResponse>(`/api/v1/vendors/${vendorId}`, {
        params: { limit: 25, offset },
      });
      return response.data;
    },
    retry: 1,
  });
}

/** PATCH contact information (email, address, tax id, notes). */
export function useUpdateVendor(vendorId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      email?: string | null;
      address?: string | null;
      tax_identifier?: string | null;
      notes?: string | null;
    }): Promise<VendorDetailResponse> => {
      const response = await api.patch<VendorDetailResponse>(
        `/api/v1/vendors/${vendorId}`,
        input
      );
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["vendor-detail", vendorId] });
      queryClient.invalidateQueries({ queryKey: ["vendors-directory"] });
    },
  });
}
