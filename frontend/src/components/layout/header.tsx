"use client";

import { LogOut, Menu } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { useWorkspace } from "@/components/workspace-provider";
import { WorkspaceSwitcher } from "@/components/layout/workspace-switcher";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

interface HeaderProps {
  onMenuClick: () => void;
}

/** Top header bar: mobile menu, workspace switcher, profile + logout. */
export function Header({ onMenuClick }: HeaderProps) {
  const router = useRouter();
  const { profile } = useWorkspace();
  const [signingOut, setSigningOut] = useState(false);

  const handleSignOut = async () => {
    setSigningOut(true);
    await getSupabaseBrowserClient().auth.signOut();
    router.replace("/login");
  };

  const initial = profile?.email?.charAt(0).toUpperCase() ?? "?";

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b bg-background px-4">
      <Button
        variant="ghost"
        size="icon"
        className="lg:hidden"
        onClick={onMenuClick}
        aria-label="Open navigation menu"
      >
        <Menu className="size-5" />
      </Button>

      <WorkspaceSwitcher />

      <div className="ml-auto flex items-center gap-2">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="flex size-8 items-center justify-center rounded-full border bg-muted text-sm font-semibold text-foreground hover:bg-accent"
              aria-label="Open profile menu"
            >
              {initial}
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="truncate font-normal">
              <span className="block text-sm font-medium">{profile?.email ?? "Signed in user"}</span>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="cursor-pointer text-destructive focus:text-destructive"
              disabled={signingOut}
              onClick={handleSignOut}
            >
              <LogOut className="size-4" aria-hidden />
              {signingOut ? "Signing out…" : "Log out"}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
