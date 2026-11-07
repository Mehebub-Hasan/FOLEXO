"use client";

import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";

import { requirePublicEnv } from "@/lib/env";

let browserClient: SupabaseClient | undefined;

/**
 * Singleton Supabase client for browser code (client components).
 * Lazily constructed on first call so importing modules never triggers
 * client creation at build/prerender time without env vars.
 */
export function getSupabaseBrowserClient(): SupabaseClient {
  if (!browserClient) {
    browserClient = createBrowserClient(
      requirePublicEnv("NEXT_PUBLIC_SUPABASE_URL"),
      requirePublicEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    );
  }
  return browserClient;
}
