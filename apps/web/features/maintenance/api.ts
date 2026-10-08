"use client";

import {
  type MaintenanceCategory,
  type MaintenanceStatus,
  type MaintenanceView,
  pageSchema,
  type TaskPriority,
  type WorkOrder,
  workOrderCountsSchema,
  workOrderSchema,
} from "@stayops/shared";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";

const workOrderPage = pageSchema(workOrderSchema);

export type WorkOrderFilters = {
  view?: MaintenanceView | "active";
  status?: MaintenanceStatus[];
  priority?: TaskPriority[];
  category?: MaintenanceCategory[];
  property_id?: string;
  unit_id?: string;
  assignee?: string;
  source_task_id?: string;
  reservation_id?: string;
  q?: string;
  limit?: number;
  offset?: number;
};

export function useWorkOrders(filters: WorkOrderFilters, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    enabled,
    queryKey: queryKeys.maintenance.list(filters),
    queryFn: ({ signal }) =>
      api("/api/v1/maintenance", workOrderPage, { signal, query: { ...filters, limit: filters.limit ?? 50, offset: filters.offset ?? 0 } }),
    placeholderData: keepPreviousData,
  });
}

export function useWorkOrderCounts(filters: Omit<WorkOrderFilters, "view" | "limit" | "offset">) {
  return useQuery({
    queryKey: queryKeys.maintenance.counts(filters),
    queryFn: ({ signal }) => api("/api/v1/maintenance/counts", workOrderCountsSchema, { signal, query: filters }),
    placeholderData: keepPreviousData,
  });
}

export function useWorkOrder(id: string | null) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: queryKeys.maintenance.detail(id ?? ""),
    queryFn: ({ signal }) => api(`/api/v1/maintenance/${id}`, workOrderSchema, { signal }),
  });
}

function useOnSaved() {
  const qc = useQueryClient();
  return (order: WorkOrder) => {
    qc.setQueryData(queryKeys.maintenance.detail(order.id), order);
    // A work order can block a unit: availability, unit state and boards all follow.
    for (const key of [
      queryKeys.maintenance.all,
      queryKeys.cleanings.all,
      queryKeys.operations.all,
      queryKeys.units.all,
      queryKeys.properties.all,
      queryKeys.reservations.all,
      queryKeys.commandCenter,
      queryKeys.audit.all,
    ]) {
      void qc.invalidateQueries({ queryKey: key });
    }
  };
}

export type WorkOrderCreateInput = {
  title: string;
  description?: string;
  category: MaintenanceCategory;
  priority: TaskPriority;
  unit_id?: string;
  reservation_id?: string;
  source_task_id?: string;
  blocks_unit: boolean;
  assigned_to?: string;
};

export function useCreateWorkOrder() {
  const onSaved = useOnSaved();
  return useMutation({
    meta: { inlineErrors: true },
    mutationFn: ({ body, idempotencyKey }: { body: WorkOrderCreateInput; idempotencyKey: string }) =>
      api("/api/v1/maintenance", workOrderSchema, { method: "POST", body, idempotencyKey }),
    onSuccess: onSaved,
  });
}

export function useUpdateWorkOrder(id: string) {
  const onSaved = useOnSaved();
  return useMutation({
    mutationFn: (body: Partial<Pick<WorkOrder, "title" | "category" | "priority" | "blocks_unit">> & { description?: string | null }) =>
      api(`/api/v1/maintenance/${id}`, workOrderSchema, { method: "PATCH", body }),
    onSuccess: onSaved,
  });
}

export type WorkOrderActionName = "start" | "block" | "resolve" | "close";

export function useWorkOrderAction(id: string) {
  const onSaved = useOnSaved();
  return useMutation({
    mutationFn: ({ action, notes }: { action: WorkOrderActionName; notes?: string }) =>
      api(`/api/v1/maintenance/${id}/${action}`, workOrderSchema, { method: "POST", body: notes ? { notes } : {} }),
    onSuccess: onSaved,
  });
}

export function useAssignWorkOrder(id: string) {
  const onSaved = useOnSaved();
  return useMutation({
    mutationFn: (assigneeId: string | null) =>
      api(`/api/v1/maintenance/${id}/assign`, workOrderSchema, { method: "POST", body: { assignee_id: assigneeId } }),
    onSuccess: onSaved,
  });
}
