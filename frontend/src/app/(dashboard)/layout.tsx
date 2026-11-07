import { DashboardShell } from "@/components/layout/dashboard-shell";

/**
 * Protected shell for the authenticated app. Route protection + session
 * refresh happen in src/middleware.ts; this layout renders the chrome.
 */
export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return <DashboardShell>{children}</DashboardShell>;
}
