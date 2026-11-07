"use client";

import {
  BarChart3,
  Building2,
  FileText,
  LayoutDashboard,
  type LucideIcon,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { branding } from "@/config/branding";
import { Button } from "@/components/ui/button";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

const NAV_ITEMS: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/invoices", label: "Invoices", icon: FileText },
  { href: "/vendors", label: "Vendors", icon: Building2 },
  { href: "/reports", label: "Reports", icon: BarChart3 },
  { href: "/settings", label: "Settings", icon: Settings },
];

interface SidebarProps {
  collapsed: boolean;
  onToggleCollapsed: () => void;
  mobileOpen: boolean;
  onMobileClose: () => void;
}

/**
 * Responsive navigation sidebar.
 * - Desktop: docked, collapsible to icon rail.
 * - Mobile: slide-in overlay opened from the header hamburger.
 */
export function Sidebar({ collapsed, onToggleCollapsed, mobileOpen, onMobileClose }: SidebarProps) {
  const pathname = usePathname();
  const isActive = (href: string) => pathname === href || pathname.startsWith(`${href}/`);

  const nav = (
    <nav className="flex flex-1 flex-col gap-1 px-2 py-2">
      {NAV_ITEMS.map((item) => {
        const active = isActive(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={onMobileClose}
            title={collapsed ? item.label : undefined}
            className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              active
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            } ${collapsed ? "justify-center px-2" : ""}`}
          >
            <item.icon className="size-4 shrink-0" aria-hidden />
            {!collapsed && <span className="truncate">{item.label}</span>}
          </Link>
        );
      })}
    </nav>
  );

  const brand = (
    <div className={`flex h-14 items-center border-b px-3 ${collapsed ? "justify-center px-2" : "gap-2"}`}>
      <span
        className="flex size-8 shrink-0 items-center justify-center rounded-md text-sm font-bold text-primary-foreground"
        style={{ backgroundColor: branding.colors.primary }}
        aria-hidden
      >
        {branding.appName.charAt(0)}
      </span>
      {!collapsed && (
        <span className="truncate text-sm font-semibold tracking-tight">{branding.appName}</span>
      )}
    </div>
  );

  return (
    <>
      {/* Mobile overlay */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden" role="presentation">
          <div
            className="absolute inset-0 bg-black/50"
            onClick={onMobileClose}
            aria-hidden
          />
          <aside className="absolute inset-y-0 left-0 flex w-64 flex-col border-r bg-background">
            {brand}
            {nav}
          </aside>
        </div>
      )}

      {/* Desktop docked sidebar */}
      <aside
        className={`hidden shrink-0 flex-col border-r bg-background transition-[width] duration-200 lg:flex ${
          collapsed ? "w-[68px]" : "w-60"
        }`}
      >
        {brand}
        {nav}
        <div className="border-t p-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={onToggleCollapsed}
            className={`w-full ${collapsed ? "justify-center px-2" : "justify-start"}`}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {collapsed ? (
              <PanelLeftOpen className="size-4" />
            ) : (
              <>
                <PanelLeftClose className="size-4" />
                <span>Collapse</span>
              </>
            )}
          </Button>
        </div>
      </aside>
    </>
  );
}
