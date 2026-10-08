"use client";

import { apiErrorSchema } from "@stayops/shared";
import type { z } from "zod";

import { GUEST_TOKEN_KEY } from "@/features/guest-portal/token";
import { ApiError } from "@/lib/api/client";
import { env } from "@/lib/env";

/**
 * API client for the guest portal. Guests have no StayOps account: every call
 * carries the portal link token (X-Guest-Token) and the server resolves the one
 * stay it grants. No organization or guest id is ever sent from the browser.
 */
export function getGuestToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(GUEST_TOKEN_KEY);
}

export function setGuestToken(token: string | null) {
  if (token) window.localStorage.setItem(GUEST_TOKEN_KEY, token);
  else window.localStorage.removeItem(GUEST_TOKEN_KEY);
}

export async function guestApi<S extends z.ZodTypeAny>(
  path: string,
  schema: S,
  options: { method?: "GET" | "POST"; body?: unknown; idempotencyKey?: string; signal?: AbortSignal } = {},
): Promise<z.infer<S>> {
  const token = getGuestToken();
  if (!token) throw new ApiError(401, "invalid_portal_link", "Open the guest portal from the link your host sent you.");
  const headers: Record<string, string> = { "X-Guest-Token": token };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;

  let response: Response;
  try {
    response = await fetch(`${env.apiUrl}/api/v1/guest${path}`, {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
      cache: "no-store",
    });
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError(0, "network_error", "You seem to be offline. We'll retry when you're back.");
  }
  const payload: unknown = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const parsed = apiErrorSchema.safeParse(payload);
    if (parsed.success) throw new ApiError(response.status, parsed.data.error.code, parsed.data.error.message);
    throw new ApiError(response.status, "http_error", `Request failed (${response.status}).`);
  }
  const result = schema.safeParse(payload);
  if (!result.success) throw new ApiError(500, "contract_mismatch", "Unexpected response. Please refresh.");
  return result.data;
}
