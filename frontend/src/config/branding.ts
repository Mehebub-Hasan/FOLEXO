/**
 * Centralized branding — the single source of truth for brand identity.
 *
 * Components must reference this module (or the CSS tokens it points to);
 * never hardcode the application name or colors in component code. Color
 * values live as CSS variables in `src/app/globals.css` (Tailwind v4 theme)
 * and are referenced here by var() so theming stays in one system.
 */
export const branding = {
  appName: "FOLEXO",
  tagline: "Invoice AI — accounts payable, automated.",
  supportEmail: "support@folexo.app",
  /** Brand color tokens, resolved from the Tailwind v4 CSS variables. */
  colors: {
    primary: "var(--primary)",
    primaryForeground: "var(--primary-foreground)",
    accent: "var(--accent)",
    muted: "var(--muted)",
    ring: "var(--ring)",
  },
} as const;

export type Branding = typeof branding;
