// Browser-safe configuration, inlined at build time by next.config.ts.
export const env = {
  supabaseUrl: process.env.NEXT_PUBLIC_SUPABASE_URL ?? "",
  supabasePublishableKey: process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? "",
  apiUrl: (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, ""),
};

export const isSupabaseConfigured = Boolean(env.supabaseUrl && env.supabasePublishableKey);
