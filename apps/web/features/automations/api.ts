"use client";

import {
  ALERT_STATUSES,
  type AlertStatus,
  type ApprovalStatus,
  automationApprovalSchema,
  automationCatalogSchema,
  automationOverviewSchema,
  type ExecutionStatus,
  executionDetailSchema,
  executionSchema,
  operationalAlertSchema,
  pageSchema,
  simulationSampleSchema,
  simulationSchema,
  validationResultSchema,
  type WorkflowDefinition,
  workflowDetailSchema,
  workflowDraftSchema,
  workflowSchema,
  type WorkflowStatus,
  workflowTemplateSchema,
  workflowVersionSchema,
} from "@stayops/shared";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";

const k = queryKeys.automations;

export function useCatalog() {
  return useQuery({
    queryKey: k.catalog,
    queryFn: ({ signal }) => api("/api/v1/automations/catalog", automationCatalogSchema, { signal }),
    staleTime: 60 * 60_000,
  });
}

export function useTemplates() {
  return useQuery({
    queryKey: k.templates,
    queryFn: ({ signal }) => api("/api/v1/automations/templates", z.array(workflowTemplateSchema), { signal }),
    staleTime: 60 * 60_000,
  });
}

export function useOverview() {
  return useQuery({
    queryKey: k.overview,
    queryFn: ({ signal }) => api("/api/v1/automations/overview", automationOverviewSchema, { signal }),
  });
}

export type WorkflowFilters = { status?: WorkflowStatus[]; failed_recently?: boolean; q?: string; limit?: number; offset?: number };

export function useWorkflows(filters: WorkflowFilters) {
  return useQuery({
    queryKey: k.list(filters),
    queryFn: ({ signal }) =>
      api("/api/v1/automations", pageSchema(workflowSchema), {
        signal,
        query: {
          status: filters.status,
          failed_recently: filters.failed_recently ? "true" : undefined,
          q: filters.q,
          limit: filters.limit ?? 50,
          offset: filters.offset ?? 0,
        },
      }),
    placeholderData: keepPreviousData,
  });
}

export function useWorkflow(id: string | null) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: k.workflow(id ?? ""),
    queryFn: ({ signal }) => api(`/api/v1/automations/${id}`, workflowDetailSchema, { signal }),
  });
}

export function useVersions(id: string) {
  return useQuery({
    queryKey: k.versions(id),
    queryFn: ({ signal }) => api(`/api/v1/automations/${id}/versions`, z.array(workflowVersionSchema), { signal }),
  });
}

export function useSamples(id: string, triggerType: string, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    enabled,
    queryKey: k.samples(id, triggerType),
    queryFn: ({ signal }) =>
      api(`/api/v1/automations/${id}/samples`, z.array(simulationSampleSchema), { signal, query: { trigger_type: triggerType } }),
  });
}

export type ExecutionFilters = { workflow_id?: string; status?: ExecutionStatus[]; limit?: number; offset?: number };

export function useExecutions(filters: ExecutionFilters) {
  return useQuery({
    queryKey: k.executionList(filters),
    queryFn: ({ signal }) =>
      api("/api/v1/automation-executions", pageSchema(executionSchema), {
        signal,
        query: { ...filters, limit: filters.limit ?? 50, offset: filters.offset ?? 0 },
      }),
    placeholderData: keepPreviousData,
  });
}

export function useExecution(id: string | null) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: k.execution(id ?? ""),
    queryFn: ({ signal }) => api(`/api/v1/automation-executions/${id}`, executionDetailSchema, { signal }),
    // Realtime drives progress; while a run is in flight a slow poll is the safety net.
    refetchInterval: (q) => (q.state.data && ["PENDING", "RUNNING"].includes(q.state.data.status) ? 4_000 : false),
  });
}

export function useApprovals(filters: { status?: ApprovalStatus[]; limit?: number }) {
  return useQuery({
    queryKey: k.approvalList(filters),
    queryFn: ({ signal }) =>
      api("/api/v1/automation-approvals", pageSchema(automationApprovalSchema), {
        signal,
        query: { status: filters.status, limit: filters.limit ?? 50 },
      }),
  });
}

export function useAlerts(filters: { status?: AlertStatus[]; limit?: number }) {
  return useQuery({
    queryKey: k.alertList(filters),
    queryFn: ({ signal }) =>
      api("/api/v1/operational-alerts", pageSchema(operationalAlertSchema), {
        signal,
        query: { status: filters.status ?? [...ALERT_STATUSES], limit: filters.limit ?? 50 },
      }),
  });
}

function useInvalidate() {
  const qc = useQueryClient();
  return (...keys: readonly (readonly unknown[])[]) => {
    for (const key of [k.all, ...keys]) void qc.invalidateQueries({ queryKey: key });
  };
}

export function useCreateWorkflow() {
  const invalidate = useInvalidate();
  return useMutation({
    meta: { inlineErrors: true },
    mutationFn: (body: { name: string; description?: string | null; definition?: WorkflowDefinition }) =>
      api("/api/v1/automations", workflowDetailSchema, { method: "POST", body }),
    onSuccess: () => invalidate(),
  });
}

export function useUseTemplate() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (key: string) => api(`/api/v1/automations/templates/${key}/use`, workflowDetailSchema, { method: "POST" }),
    onSuccess: () => invalidate(),
  });
}

export function useSaveWorkflow(id: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: { inlineErrors: true },
    mutationFn: (body: { name?: string; description?: string | null; definition?: WorkflowDefinition; expected_revision?: number }) =>
      api(`/api/v1/automations/${id}`, workflowDetailSchema, { method: "PATCH", body }),
    onSuccess: (wf) => {
      qc.setQueryData(k.workflow(id), wf);
      void qc.invalidateQueries({ queryKey: k.workflows });
    },
  });
}

export function useWorkflowAction(id: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: { inlineErrors: true },
    mutationFn: ({ action, revision }: { action: "publish" | "pause" | "resume" | "archive"; revision?: number }) =>
      api(`/api/v1/automations/${id}/${action}`, workflowDetailSchema, {
        method: "POST",
        body: revision ? { expected_revision: revision } : undefined,
      }),
    onSuccess: (wf) => {
      qc.setQueryData(k.workflow(id), wf);
      for (const key of [k.workflows, k.overview, k.versions(id), queryKeys.audit.all]) void qc.invalidateQueries({ queryKey: key });
    },
  });
}

export function useValidate(id: string) {
  return useMutation({
    mutationFn: (definition: WorkflowDefinition) =>
      api(`/api/v1/automations/${id}/validate`, validationResultSchema, { method: "POST", body: { definition } }),
  });
}

export function useSimulate(id: string) {
  return useMutation({
    meta: { inlineErrors: true },
    mutationFn: (body: { definition: WorkflowDefinition; event_id?: string; entity_id?: string }) =>
      api(`/api/v1/automations/${id}/simulate`, simulationSchema, { method: "POST", body }),
  });
}

export function useRetryExecution(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api(`/api/v1/automation-executions/${id}/retry`, executionDetailSchema, { method: "POST" }),
    onSuccess: (detail) => {
      qc.setQueryData(k.execution(id), detail);
      for (const key of [k.executions, k.overview, k.workflows, k.approvals]) void qc.invalidateQueries({ queryKey: key });
    },
  });
}

export function useDecideApproval(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ approve, version }: { approve: boolean; version: number }) =>
      api(`/api/v1/automation-approvals/${id}/${approve ? "approve" : "reject"}`, automationApprovalSchema, {
        method: "POST",
        body: { expected_version: version },
      }),
    onSuccess: () => {
      for (const key of [k.all, queryKeys.tasks.all, queryKeys.maintenance.all, queryKeys.inbox.all, queryKeys.commandCenter, queryKeys.audit.all]) {
        void qc.invalidateQueries({ queryKey: key });
      }
    },
  });
}

export function useAlertAction(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (action: "acknowledge" | "resolve") => api(`/api/v1/operational-alerts/${id}/${action}`, operationalAlertSchema, { method: "POST" }),
    onSuccess: () => {
      for (const key of [k.alerts, k.overview, queryKeys.commandCenter]) void qc.invalidateQueries({ queryKey: key });
    },
  });
}

export function useDraftWithAI() {
  return useMutation({
    meta: { inlineErrors: true },
    mutationFn: (prompt: string) => api("/api/v1/automations/draft-with-ai", workflowDraftSchema, { method: "POST", body: { prompt } }),
  });
}
