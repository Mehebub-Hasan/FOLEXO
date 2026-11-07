import Link from "next/link";

import { branding } from "@/config/branding";

/**
 * Centered shell for /login and /register. Authenticated users never see
 * these routes — middleware.ts bounces them to /dashboard.
 */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-8 p-6">
      <div className="flex flex-col items-center gap-2 text-center">
        <span
          className="flex size-12 items-center justify-center rounded-lg text-xl font-bold text-primary-foreground"
          style={{ backgroundColor: branding.colors.primary }}
          aria-hidden
        >
          {branding.appName.charAt(0)}
        </span>
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{branding.appName}</h1>
          <p className="text-sm text-muted-foreground">{branding.tagline}</p>
        </div>
      </div>

      <div className="w-full max-w-sm">{children}</div>

      <p className="text-xs text-muted-foreground">
        Need help?{" "}
        <Link href={`mailto:${branding.supportEmail}`} className="underline underline-offset-4">
          {branding.supportEmail}
        </Link>
      </p>
    </div>
  );
}
