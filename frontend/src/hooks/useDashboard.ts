"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { DashboardSummary } from "@/lib/api-types";

/** Workspace dashboard KPIs + recent activity + top suppliers. */
export function useDashboardSummary() {
  return useQuery({
    queryKey: ["dashboard-summary"],
    queryFn: async (): Promise<DashboardSummary> => {
      const response = await api.get<DashboardSummary>("/api/v1/dashboard/summary");
      return response.data;
    },
    staleTime: 15_000,
  });
}

/** Trigger a browser download of an export through the authed API client. */
export async function downloadExport(path: string, filename: string): Promise<void> {
  const response = await api.get(path, { responseType: "blob" });
  const url = URL.createObjectURL(response.data as Blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
