/**
 * Active-workspace persistence (localStorage) as a tiny external store.
 *
 * Kept separate from the React context so non-React modules (the axios
 * interceptor in lib/api.ts) can read the active workspace id without
 * importing component code. The WorkspaceProvider subscribes via
 * useSyncExternalStore; writes go through setActiveWorkspaceId so all
 * subscribers re-render.
 */
const STORAGE_KEY = "folexo.activeWorkspaceId";

const listeners = new Set<() => void>();

export function getActiveWorkspaceId(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(STORAGE_KEY);
}

/** Subscription factory for useSyncExternalStore (includes cross-tab sync). */
export function subscribeToActiveWorkspace(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

/** Write + notify subscribers. Pass null to clear the selection. */
export function setActiveWorkspaceId(workspaceId: string | null): void {
  if (typeof window === "undefined") return;
  if (workspaceId === null) {
    window.localStorage.removeItem(STORAGE_KEY);
  } else {
    window.localStorage.setItem(STORAGE_KEY, workspaceId);
  }
  for (const listener of listeners) listener();
}
