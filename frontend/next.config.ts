import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /*
   * Cache Components (static prerendering + partial streaming) is disabled:
   * every dashboard route sits behind auth and is request-scoped (JWT,
   * workspace header), so static prerendering buys nothing and fights every
   * client navigation hook (usePathname/useParams) under dynamic routes.
   * Revisit for public marketing pages post-MVP.
   */
  cacheComponents: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
