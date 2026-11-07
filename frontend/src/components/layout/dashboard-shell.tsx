"use client";

import { useState } from "react";

import { Header } from "@/components/layout/header";
import { Sidebar } from "@/components/layout/sidebar";
import { WorkspaceProvider } from "@/components/workspace-provider";

/**
 * Client shell for the (dashboard) route group: workspace context + the
 * sidebar/header chrome. The (dashboard)/layout.tsx server component simply
 * wraps its children in this. Collapse state is session-only (in memory) to
 * avoid localStorage hydration mismatches.
 */
export function DashboardShell({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <WorkspaceProvider>
      <div className="flex min-h-screen">
        <Sidebar
          collapsed={collapsed}
          onToggleCollapsed={() => setCollapsed((previous) => !previous)}
          mobileOpen={mobileOpen}
          onMobileClose={() => setMobileOpen(false)}
        />
        <div className="flex min-w-0 flex-1 flex-col">
          <Header onMenuClick={() => setMobileOpen(true)} />
          <main className="flex-1 p-6">{children}</main>
        </div>
      </div>
    </WorkspaceProvider>
  );
}
