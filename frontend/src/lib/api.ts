import axios from "axios";

import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { getActiveWorkspaceId } from "@/lib/workspace-storage";

/**
 * Central API client for the FOLEXO backend.
 *
 * Every request automatically carries:
 * - `Authorization: Bearer <supabase_access_token>` (from the browser session)
 * - `X-Workspace-ID: <active_workspace_id>` (from localStorage; see the
 *   WorkspaceProvider for how the value is set)
 *
 * The backend re-verifies the JWT and re-checks workspace membership on every
 * request — these headers are a convenience, not a security boundary.
 */
export const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
  headers: { "Content-Type": "application/json" },
});

api.interceptors.request.use(async (config) => {
  const supabase = getSupabaseBrowserClient();
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  const workspaceId = getActiveWorkspaceId();
  if (workspaceId) {
    config.headers["X-Workspace-ID"] = workspaceId;
  }
  return config;
});

/** Extract a user-presentable message from an API error. */
export function apiErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = (error.response?.data as { detail?: string } | undefined)?.detail;
    return detail ?? error.message;
  }
  if (error instanceof Error) return error.message;
  return "Something went wrong. Please try again.";
}
