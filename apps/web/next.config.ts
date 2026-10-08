import path from "node:path";

import { loadEnvConfig } from "@next/env";
import type { NextConfig } from "next";

// Single source of configuration: the repo-root `.env` (shared with apps/api).
// forceReload is required: Next has already called loadEnvConfig for apps/web
// (which has no .env) and @next/env returns that cached result otherwise.
const repoRoot = path.resolve(process.cwd(), "../..");
loadEnvConfig(repoRoot, process.env.NODE_ENV !== "production", console, true);

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || process.env.SUPABASE_URL || "";
const supabaseKey =
  process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY || process.env.SUPABASE_PUBLISHABLE_KEY || "";
const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  reactCompiler: true,
  transpilePackages: ["@stayops/shared"],
  turbopack: { root: repoRoot },
  // Only browser-safe values are exposed. Secret keys are never referenced here.
  env: {
    NEXT_PUBLIC_SUPABASE_URL: supabaseUrl,
    NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: supabaseKey,
    NEXT_PUBLIC_API_URL: apiUrl,
  },
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
