"use client";

import {
  type GuestRequestType,
  guestRequestSchema,
  type MaintenanceCategory,
  pageSchema,
  type TaskPriority,
} from "@stayops/shared";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";

const requestPage = pageSchema(guestRequestSchema);

export type GuestRequestFilters = {
  view?: "all" | "open" | "in_progress" | "unresolved" | "resolved";
  conversation_id?: string;
  reservation_id?: string;
  guest_id?: string;
  limit?: number;
};

export function useGuestRequests(filters: GuestRequestFilters, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    enabled,
    queryKey: queryKeys.guestRequests.list(filters),
    queryFn: ({ signal }) => api("/api/v1/guest-requests", requestPage, { signal, query: { ...filters, limit: filters.limit ?? 50 } }),
    placeholderData: keepPreviousData,
  });
}

/** A request change moves the inbox (counts, thread, context) and possibly operations. */
function useRequestRefresh() {
  const qc = useQueryClient();
  return () => {
    for (const key of [queryKeys.guestRequests.all, queryKeys.inbox.all, queryKeys.tasks.all, queryKeys.maintenance.all, queryKeys.commandCenter, queryKeys.audit.all]) {
      void qc.invalidateQueries({ queryKey: key });
    }
  };
}

export function useCreateGuestRequest() {
  const refresh = useRequestRefresh();
  return useMutation({
    meta: { inlineErrors: true },
    mutationFn: (body: { conversation_id: string; type: GuestRequestType; description: string; priority: TaskPriority; requested_for?: string }) =>
      api("/api/v1/guest-requests", guestRequestSchema, { method: "POST", body }),
    onSuccess: refresh,
  });
}

export type RequestActionName = "acknowledge" | "start" | "resolve" | "close" | "cancel";

export function useRequestAction(id: string) {
  const refresh = useRequestRefresh();
  return useMutation({
    mutationFn: ({ action, note, notifyGuest = true, expectedVersion }: { action: RequestActionName; note?: string; notifyGuest?: boolean; expectedVersion?: number }) =>
      api(`/api/v1/guest-requests/${id}/${action}`, guestRequestSchema, {
        method: "POST",
        body: { notify_guest: notifyGuest, ...(note ? { note } : {}), ...(expectedVersion ? { expected_version: expectedVersion } : {}) },
      }),
    onSuccess: refresh,
  });
}

export function useConvertToTask(id: string) {
  const refresh = useRequestRefresh();
  return useMutation({
    meta: { inlineErrors: true },
    mutationFn: (body: { title?: string; priority?: TaskPriority; assigned_to?: string; due_at?: string }) =>
      api(`/api/v1/guest-requests/${id}/convert-to-task`, guestRequestSchema, { method: "POST", body }),
    onSuccess: refresh,
  });
}

export function useConvertToMaintenance(id: string) {
  const refresh = useRequestRefresh();
  return useMutation({
    meta: { inlineErrors: true },
    mutationFn: (body: { title?: string; category: MaintenanceCategory; priority?: TaskPriority; blocks_unit: boolean; assigned_to?: string }) =>
      api(`/api/v1/guest-requests/${id}/convert-to-maintenance`, guestRequestSchema, { method: "POST", body }),
    onSuccess: refresh,
  });
}
