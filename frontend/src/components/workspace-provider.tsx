"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useSyncExternalStore,
} from "react";

import { api } from "@/lib/api";
import {
  getActiveWorkspaceId,
  setActiveWorkspaceId,
  subscribeToActiveWorkspace,
} from "@/lib/workspace-storage";

export interface WorkspaceSummary {
  id: string;
  name: string;
  slug: string;
  role: string;
  default_currency?: string;
}

export interface AuthMeResponse {
  profile: {
    id: string;
    email: string;
  };
  workspaces: WorkspaceSummary[];
}

interface WorkspaceContextValue {
  profile: AuthMeResponse["profile"] | null;
  workspaces: WorkspaceSummary[];
  activeWorkspace: WorkspaceSummary | null;
  activeWorkspaceId: string | null;
  setActiveWorkspace: (workspaceId: string) => void;
  isLoading: boolean;
  isError: boolean;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

/**
 * Loads the authenticated user's profile + workspaces (GET /api/v1/auth/me)
 * and tracks the active workspace id (localStorage external store read via
 * useSyncExternalStore — no effects, no hydration mismatches).
 *
 * Stale stored ids (e.g. after losing membership) are corrected in the query
 * pipeline (event context): the fallback is the first workspace, or null
 * when the user has none.
 */
export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();

  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: async (): Promise<AuthMeResponse> => {
      const response = await api.get<AuthMeResponse>("/api/v1/auth/me");
      const data = response.data;
      const stored = getActiveWorkspaceId();
      const storedIsValid =
        stored !== null && data.workspaces.some((workspace) => workspace.id === stored);
      if (!storedIsValid) {
        setActiveWorkspaceId(data.workspaces[0]?.id ?? null);
      }
      return data;
    },
    retry: false,
  });

  const storedWorkspaceId = useSyncExternalStore(
    subscribeToActiveWorkspace,
    getActiveWorkspaceId,
    () => null
  );

  const workspaces = useMemo(() => meQuery.data?.workspaces ?? [], [meQuery.data]);

  // Derived (render-safe): stored selection when valid, else the first
  // workspace, else null.
  const activeWorkspace = useMemo(() => {
    if (storedWorkspaceId) {
      const match = workspaces.find((workspace) => workspace.id === storedWorkspaceId);
      if (match) return match;
    }
    return workspaces[0] ?? null;
  }, [storedWorkspaceId, workspaces]);

  const setActiveWorkspace = useCallback(
    (workspaceId: string) => {
      setActiveWorkspaceId(workspaceId);
      // Every workspace-scoped query depends on the active workspace.
      queryClient.invalidateQueries();
    },
    [queryClient]
  );

  const value = useMemo<WorkspaceContextValue>(
    () => ({
      profile: meQuery.data?.profile ?? null,
      workspaces,
      activeWorkspace,
      activeWorkspaceId: activeWorkspace?.id ?? null,
      setActiveWorkspace,
      isLoading: meQuery.isPending,
      isError: meQuery.isError,
    }),
    [meQuery.data, meQuery.isPending, meQuery.isError, workspaces, activeWorkspace, setActiveWorkspace]
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceContextValue {
  const context = useContext(WorkspaceContext);
  if (!context) {
    throw new Error("useWorkspace must be used within a WorkspaceProvider");
  }
  return context;
}
