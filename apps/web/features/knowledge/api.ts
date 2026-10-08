"use client";

import { type KnowledgeCategory, knowledgeEntrySchema, type KnowledgeVisibility } from "@stayops/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";

export function useKnowledge(propertyId: string, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    enabled,
    queryKey: queryKeys.knowledge.list(propertyId),
    queryFn: ({ signal }) => api(`/api/v1/properties/${propertyId}/knowledge`, z.array(knowledgeEntrySchema), { signal }),
  });
}

export type KnowledgeInput = {
  category: KnowledgeCategory;
  title: string;
  content: string;
  visibility: KnowledgeVisibility;
  unit_id?: string | null;
};

function useRefresh() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: queryKeys.knowledge.all });
    void qc.invalidateQueries({ queryKey: queryKeys.audit.all });
  };
}

export function useSaveKnowledge(propertyId: string) {
  const refresh = useRefresh();
  return useMutation({
    meta: { inlineErrors: true },
    mutationFn: ({ id, body, version }: { id?: string; body: KnowledgeInput; version?: number }) =>
      id
        ? api(`/api/v1/knowledge/${id}`, knowledgeEntrySchema, {
            method: "PATCH",
            body: { category: body.category, title: body.title, content: body.content, visibility: body.visibility, expected_version: version },
          })
        : api(`/api/v1/properties/${propertyId}/knowledge`, knowledgeEntrySchema, {
            method: "POST",
            body: { ...body, unit_id: body.unit_id || undefined },
          }),
    onSuccess: refresh,
  });
}

export function useArchiveKnowledge() {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (id: string) => api(`/api/v1/knowledge/${id}/archive`, z.null(), { method: "POST" }),
    onSuccess: refresh,
  });
}
