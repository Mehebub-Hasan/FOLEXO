"use client";

import { Building2, Check, ChevronsUpDown, Plus } from "lucide-react";

import { useWorkspace } from "@/components/workspace-provider";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

/**
 * Workspace (organization) switcher in the dashboard header.
 * The selection lives in the WorkspaceProvider (React state + localStorage)
 * and is attached to every API call as `X-Workspace-ID` by lib/api.ts.
 */
export function WorkspaceSwitcher() {
  const { workspaces, activeWorkspace, activeWorkspaceId, setActiveWorkspace, isLoading, isError } =
    useWorkspace();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex h-8 max-w-56 items-center gap-2 rounded-md border bg-background px-2 text-sm font-medium hover:bg-muted disabled:opacity-60"
          disabled={isLoading}
          aria-label="Switch workspace"
        >
          <Building2 className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <span className="truncate">{activeWorkspace ? activeWorkspace.name : "No workspace"}</span>
          <ChevronsUpDown className="ml-auto size-3.5 shrink-0 text-muted-foreground" aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56">
        <DropdownMenuLabel>Workspaces</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {isError && (
          <div className="px-2 py-1.5 text-xs text-muted-foreground">
            Could not load workspaces. Is the backend running?
          </div>
        )}
        {!isError && workspaces.length === 0 && !isLoading && (
          <div className="px-2 py-1.5 text-xs text-muted-foreground">
            No workspaces yet — create one from the CLI once invited, or via POST /api/v1/workspaces.
          </div>
        )}
        {workspaces.map((workspace) => (
          <DropdownMenuItem
            key={workspace.id}
            onClick={() => setActiveWorkspace(workspace.id)}
            className="cursor-pointer"
          >
            <span className="truncate">{workspace.name}</span>
            <span className="ml-auto text-xs text-muted-foreground">{workspace.role}</span>
            {workspace.id === activeWorkspaceId && <Check className="size-4 shrink-0" aria-hidden />}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem disabled className="cursor-pointer">
          <Plus className="size-4" aria-hidden />
          New workspace
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
