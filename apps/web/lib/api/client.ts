"use client";

import { apiErrorSchema } from "@stayops/shared";
import type { z } from "zod";

import { env } from "@/lib/env";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId?: string | null,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }

  get isConflict() {
    return this.status === 409;
  }
}

const ORG_STORAGE_KEY = "stayops.activeOrganizationId"; // non-sensitive UI preference

export function getActiveOrganizationId(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(ORG_STORAGE_KEY);
}

export function setActiveOrganizationId(id: string | null) {
  if (id) window.localStorage.setItem(ORG_STORAGE_KEY, id);
  else window.localStorage.removeItem(ORG_STORAGE_KEY);
}

type RequestOptions = {
  method?: "GET" | "POST" | "PATCH";
  body?: unknown;
  query?: Record<string, string | number | string[] | undefined | null>;
  idempotencyKey?: string;
  signal?: AbortSignal;
};

async function accessToken(): Promise<string | null> {
  // The session lives in cookies; supabase-js refreshes it transparently.
  const { data } = await getSupabaseBrowserClient().auth.getSession();
  return data.session?.access_token ?? null;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = new URL(`${env.apiUrl}${path}`);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) value.forEach((v) => url.searchParams.append(key, v));
    else url.searchParams.set(key, String(value));
  }
  return url.toString();
}

/**
 * Typed API call: attaches the bearer token and active organization, parses the
 * consistent error envelope into ApiError, and validates the response body.
 */
export async function api<S extends z.ZodTypeAny>(
  path: string,
  schema: S,
  options: RequestOptions = {},
): Promise<z.infer<S>> {
  const token = await accessToken();
  if (!token) throw new ApiError(401, "unauthenticated", "Your session has expired. Sign in again.");

  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  const orgId = getActiveOrganizationId();
  if (orgId) headers["X-Organization-Id"] = orgId;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;

  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
      cache: "no-store",
    });
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError(0, "network_error", "Can't reach the StayOps API. Check your connection.");
  }

  const requestId = response.headers.get("x-request-id");
  const payload: unknown = response.status === 204 ? null : await response.json().catch(() => null);

  if (!response.ok) {
    const parsed = apiErrorSchema.safeParse(payload);
    if (parsed.success) {
      const { code, message, details } = parsed.data.error;
      throw new ApiError(response.status, code, message, requestId, details);
    }
    throw new ApiError(response.status, "http_error", `Request failed (${response.status}).`, requestId);
  }

  const result = schema.safeParse(payload);
  if (!result.success) {
    console.error("API contract mismatch", path, result.error.issues);
    throw new ApiError(500, "contract_mismatch", "Unexpected response from the API.", requestId);
  }
  return result.data;
}

export function newIdempotencyKey(prefix = "web"): string {
  return `${prefix}-${crypto.randomUUID()}`;
}
