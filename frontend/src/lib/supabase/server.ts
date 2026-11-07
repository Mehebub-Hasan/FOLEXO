import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

import { requirePublicEnv } from "@/lib/env";

/**
 * Supabase client for Server Components and Route Handlers.
 * Reads cookies from the request via next/headers. Cookie writes are only
 * possible in Server Actions and Route Handlers; in Server Components the
 * write attempt is a no-op (supabase-js handles refreshed tokens it cannot
 * persist by retrying reads through the middleware-refreshed cookie).
 */
export async function getSupabaseServerClient() {
  const cookieStore = await cookies();

  return createServerClient(
    requirePublicEnv("NEXT_PUBLIC_SUPABASE_URL"),
    requirePublicEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY"),
    {
      cookies: {
        getAll() {
          return cookieStore.getAll();
        },
        setAll(cookiesToSet) {
          try {
            for (const { name, value, options } of cookiesToSet) {
              cookieStore.set(name, value, options);
            }
          } catch {
            // Called from a Server Component — safe to ignore; the session
            // refresh is persisted by middleware on the next navigation.
          }
        },
      },
    }
  );
}
