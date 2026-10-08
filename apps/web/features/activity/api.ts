"use client";

import { auditLogSchema, commandCenterSchema, eventSchema, pageSchema } from "@stayops/shared";
import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";

const auditPage = pageSchema(auditLogSchema);
const eventPage = pageSchema(eventSchema);

export type AuditFilters = {
  entity_type?: string | readonly string[];
  entity_id?: string;
  q?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
};

export function useAudit(filters: AuditFilters, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.audit.list(filters),
    queryFn: ({ signal }) => {
      const { entity_type, ...rest } = filters;
      return api("/api/v1/audit", auditPage, {
        signal,
        query: {
          limit: 50,
          ...rest,
          entity_type: typeof entity_type === "string" ? entity_type : entity_type ? [...entity_type] : undefined,
        },
      });
    },
    placeholderData: keepPreviousData,
    enabled,
  });
}

export type EventFilters = { aggregate_id?: string; event_type?: string; limit?: number; offset?: number };

export function useEvents(filters: EventFilters) {
  return useQuery({
    queryKey: queryKeys.events.list(filters),
    queryFn: ({ signal }) => api("/api/v1/events", eventPage, { signal, query: { limit: 50, ...filters } }),
    placeholderData: keepPreviousData,
  });
}

export function useCommandCenter() {
  return useQuery({
    queryKey: queryKeys.commandCenter,
    queryFn: ({ signal }) => api("/api/v1/command-center", commandCenterSchema, { signal }),
    // Time-derived signals (late arrivals) change without writes: re-evaluate each minute.
    refetchInterval: 60_000,
  });
}

/** Paged audit timeline ("Load more"), filters as in useAudit. */
export function useAuditInfinite(filters: Omit<AuditFilters, "offset" | "limit">, pageSize = 50) {
  return useInfiniteQuery({
    queryKey: [...queryKeys.audit.list(filters), "infinite", pageSize],
    initialPageParam: 0,
    queryFn: ({ signal, pageParam }) => {
      const { entity_type, ...rest } = filters;
      return api("/api/v1/audit", auditPage, {
        signal,
        query: {
          ...rest,
          limit: pageSize,
          offset: pageParam,
          entity_type: typeof entity_type === "string" ? entity_type : entity_type ? [...entity_type] : undefined,
        },
      });
    },
    getNextPageParam: (last) => (last.offset + last.items.length < last.total ? last.offset + last.items.length : undefined),
    placeholderData: keepPreviousData,
  });
}
