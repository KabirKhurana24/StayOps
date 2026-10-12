"use client";

import { aiProposalSchema, aiRunSchema, aiStatusSchema } from "@stayops/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";

export function useAIStatus() {
  return useQuery({ queryKey: queryKeys.ai.status, queryFn: ({ signal }) => api("/api/v1/ai/status", aiStatusSchema, { signal }), staleTime: 5 * 60_000 });
}

/** Your recent Copilot runs, or a conversation's assistant runs. Realtime keeps them live. */
export function useAIRuns(filters: { conversation_id?: string; limit?: number }, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    enabled,
    queryKey: queryKeys.ai.runs(filters),
    queryFn: ({ signal }) => api("/api/v1/ai/runs", z.array(aiRunSchema), { signal, query: { ...filters } }),
  });
}

export function useAIRun(id: string | null) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: queryKeys.ai.run(id ?? ""),
    queryFn: ({ signal }) => api(`/api/v1/ai/runs/${id}`, aiRunSchema, { signal }),
    // Realtime drives updates; while a run is live, a slow poll is the safety net.
    refetchInterval: (q) => (q.state.data?.status === "RUNNING" ? 3_000 : false),
  });
}

export function useAskCopilot() {
  const qc = useQueryClient();
  return useMutation({
    meta: { inlineErrors: true },
    mutationFn: (question: string) => api("/api/v1/copilot/ask", aiRunSchema, { method: "POST", body: { question } }),
    onSuccess: (run) => {
      qc.setQueryData(queryKeys.ai.run(run.id), run);
      void qc.invalidateQueries({ queryKey: queryKeys.ai.all });
    },
  });
}

export function useDecideProposal(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ approve, version }: { approve: boolean; version: number }) =>
      api(`/api/v1/ai/proposals/${id}/${approve ? "approve" : "reject"}`, aiProposalSchema, { method: "POST", body: { expected_version: version } }),
    onSuccess: () => {
      for (const key of [queryKeys.ai.all, queryKeys.tasks.all, queryKeys.maintenance.all, queryKeys.commandCenter, queryKeys.audit.all]) {
        void qc.invalidateQueries({ queryKey: key });
      }
    },
  });
}
