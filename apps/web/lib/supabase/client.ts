"use client";

import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";

import { env } from "@/lib/env";

let client: SupabaseClient | undefined;

/**
 * Browser Supabase client. @supabase/ssr persists the session in cookies (shared
 * with the server/proxy), not localStorage.
 */
export function getSupabaseBrowserClient(): SupabaseClient {
  client ??= createBrowserClient(env.supabaseUrl, env.supabasePublishableKey);
  return client;
}
