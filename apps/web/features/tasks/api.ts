"use client";

import {
  type Cleaning,
  pageSchema,
  type Task,
  type TaskPriority,
  type TaskStatus,
  type TaskType,
  type TaskView,
  taskCountsSchema,
  taskSchema,
} from "@stayops/shared";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";

const taskPage = pageSchema(taskSchema);

export type TaskFilters = {
  view?: TaskView;
  status?: TaskStatus[];
  priority?: TaskPriority[];
  task_type?: TaskType[];
  property_id?: string;
  unit_id?: string;
  reservation_id?: string;
  assignee?: string;
  q?: string;
  sort?: string;
  limit?: number;
  offset?: number;
};

/** Server-side filtered, sorted, paginated task board. */
export function useTasks(filters: TaskFilters, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    enabled,
    queryKey: queryKeys.tasks.list(filters),
    queryFn: ({ signal }) =>
      api("/api/v1/tasks", taskPage, { signal, query: { ...filters, limit: filters.limit ?? 50, offset: filters.offset ?? 0 } }),
    placeholderData: keepPreviousData,
  });
}

export function useTaskCounts(filters: Omit<TaskFilters, "view" | "limit" | "offset" | "sort">) {
  return useQuery({
    queryKey: queryKeys.tasks.counts(filters),
    queryFn: ({ signal }) => api("/api/v1/tasks/counts", taskCountsSchema, { signal, query: filters }),
    placeholderData: keepPreviousData,
  });
}

export function useTask(id: string | null) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: queryKeys.tasks.detail(id ?? ""),
    queryFn: ({ signal }) => api(`/api/v1/tasks/${id}`, taskSchema, { signal }),
  });
}

function useTaskCacheUpdate() {
  const qc = useQueryClient();
  return (task: Task) => {
    qc.setQueryData(queryKeys.tasks.detail(task.id), task);
    // The cleaning drawer renders actions from its embedded task: refresh that copy
    // now, or a quick next click would send a stale expected_version (409).
    if (task.cleaning) {
      qc.setQueryData<Cleaning>(queryKeys.cleanings.detail(task.cleaning.id), (prev) => (prev ? { ...prev, task } : prev));
    }
    void qc.invalidateQueries({ queryKey: queryKeys.tasks.all });
    void qc.invalidateQueries({ queryKey: queryKeys.cleanings.all });
    void qc.invalidateQueries({ queryKey: queryKeys.operations.all });
    void qc.invalidateQueries({ queryKey: queryKeys.commandCenter });
    void qc.invalidateQueries({ queryKey: queryKeys.audit.all });
  };
}

export type TaskCreateInput = {
  title: string;
  task_type: TaskType;
  priority: TaskPriority;
  description?: string;
  property_id?: string;
  unit_id?: string;
  reservation_id?: string;
  assigned_to?: string;
  due_at?: string;
};

export function useCreateTask() {
  const onSaved = useTaskCacheUpdate();
  return useMutation({
    meta: { inlineErrors: true },
    mutationFn: ({ body, idempotencyKey }: { body: TaskCreateInput; idempotencyKey: string }) =>
      api("/api/v1/tasks", taskSchema, { method: "POST", body, idempotencyKey }),
    onSuccess: onSaved,
  });
}

export function useUpdateTask(id: string) {
  const onSaved = useTaskCacheUpdate();
  return useMutation({
    mutationFn: (body: { title?: string; description?: string | null; priority?: TaskPriority; due_at?: string | null; expected_version?: number }) =>
      api(`/api/v1/tasks/${id}`, taskSchema, { method: "PATCH", body }),
    onSuccess: onSaved,
  });
}

export type TaskActionName = "start" | "complete" | "cancel";

/** Lifecycle actions; the server state machine decides what's allowed. */
export function useTaskAction(id: string) {
  const onSaved = useTaskCacheUpdate();
  return useMutation({
    mutationFn: ({ action, expectedVersion, reason }: { action: TaskActionName; expectedVersion?: number; reason?: string }) =>
      api(`/api/v1/tasks/${id}/${action}`, taskSchema, {
        method: "POST",
        body: { ...(expectedVersion ? { expected_version: expectedVersion } : {}), ...(reason ? { reason } : {}) },
      }),
    onSuccess: onSaved,
  });
}

export function useAssignTask(id: string) {
  const onSaved = useTaskCacheUpdate();
  return useMutation({
    mutationFn: (assigneeId: string | null) =>
      api(`/api/v1/tasks/${id}/assign`, taskSchema, { method: "POST", body: { assignee_id: assigneeId } }),
    onSuccess: onSaved,
  });
}
