/**
 * Public environment variable access with loud failures.
 *
 * Used by the Supabase clients (browser + middleware/server) so a missing
 * variable fails with an actionable message instead of a cryptic
 * "supabaseUrl is required" deep inside supabase-js.
 */
export function requirePublicEnv(name: "NEXT_PUBLIC_SUPABASE_URL" | "NEXT_PUBLIC_SUPABASE_ANON_KEY"): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `Missing environment variable ${name}. Copy frontend/.env.example to frontend/.env.local and fill in your Supabase project values.`
    );
  }
  return value;
}
