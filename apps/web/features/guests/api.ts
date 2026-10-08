"use client";

import { guestSchema, pageSchema } from "@stayops/shared";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, newIdempotencyKey } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";

const guestPage = pageSchema(guestSchema);

/** Guest list. `stats` adds stay history (skip it for lightweight pickers). */
export function useGuests(q: string, limit = 50, { stats = true }: { stats?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.guests.list(q, limit, stats),
    queryFn: ({ signal }) => api("/api/v1/guests", guestPage, { signal, query: { q, limit, include_stats: String(stats) } }),
    placeholderData: keepPreviousData,
  });
}

export function useGuest(id: string) {
  return useQuery({
    queryKey: queryKeys.guests.detail(id),
    queryFn: ({ signal }) => api(`/api/v1/guests/${id}`, guestSchema, { signal }),
  });
}

export function useCreateGuest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { first_name: string; last_name: string; email?: string; phone?: string }) =>
      api("/api/v1/guests", guestSchema, {
        method: "POST",
        body,
        idempotencyKey: newIdempotencyKey("guest"),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.guests.all }),
  });
}
