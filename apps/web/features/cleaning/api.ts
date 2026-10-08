"use client";

import { type Cleaning, type CleaningStatus, type CleaningView, cleaningCountsSchema, cleaningSchema, pageSchema } from "@stayops/shared";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";

const cleaningPage = pageSchema(cleaningSchema);

export type CleaningFilters = {
  view?: CleaningView | "all";
  status?: CleaningStatus[];
  property_id?: string;
  unit_id?: string;
  assignee?: string;
  q?: string;
  limit?: number;
  offset?: number;
};

export function useCleanings(filters: CleaningFilters) {
  return useQuery({
    queryKey: queryKeys.cleanings.list(filters),
    queryFn: ({ signal }) =>
      api("/api/v1/cleanings", cleaningPage, { signal, query: { ...filters, limit: filters.limit ?? 50, offset: filters.offset ?? 0 } }),
    placeholderData: keepPreviousData,
  });
}

export function useCleaningCounts(filters: Omit<CleaningFilters, "view" | "limit" | "offset">) {
  return useQuery({
    queryKey: queryKeys.cleanings.counts(filters),
    queryFn: ({ signal }) => api("/api/v1/cleanings/counts", cleaningCountsSchema, { signal, query: filters }),
    placeholderData: keepPreviousData,
  });
}

export function useCleaning(id: string | null | undefined) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: queryKeys.cleanings.detail(id ?? ""),
    queryFn: ({ signal }) => api(`/api/v1/cleanings/${id}`, cleaningSchema, { signal }),
  });
}

function useOnCleaningSaved() {
  const qc = useQueryClient();
  return (cleaning: Cleaning) => {
    qc.setQueryData(queryKeys.cleanings.detail(cleaning.id), cleaning);
    qc.setQueryData(queryKeys.tasks.detail(cleaning.task.id), cleaning.task);
    void qc.invalidateQueries({ queryKey: queryKeys.cleanings.all });
    void qc.invalidateQueries({ queryKey: queryKeys.tasks.all });
    void qc.invalidateQueries({ queryKey: queryKeys.operations.all });
    void qc.invalidateQueries({ queryKey: queryKeys.commandCenter });
    void qc.invalidateQueries({ queryKey: queryKeys.audit.all });
  };
}

/** Tick checklist items / save notes. Optimistic for the checkbox, server is authoritative. */
export function useUpdateCleaning(id: string) {
  const qc = useQueryClient();
  const onSaved = useOnCleaningSaved();
  return useMutation({
    // Run one save at a time per cleaning: the row is version-checked, so two
    // overlapping PATCHes (quick successive ticks) would make the second one 409.
    scope: { id: `cleaning:${id}` },
    mutationFn: (body: { checklist?: { id: string; completed: boolean }[]; cleaner_notes?: string | null }) =>
      api(`/api/v1/cleanings/${id}`, cleaningSchema, { method: "PATCH", body }),
    onMutate: async (body) => {
      if (!body.checklist) return;
      await qc.cancelQueries({ queryKey: queryKeys.cleanings.detail(id) });
      const previous = qc.getQueryData<Cleaning>(queryKeys.cleanings.detail(id));
      if (previous) {
        const updates = new Map(body.checklist.map((u) => [u.id, u.completed]));
        const checklist = previous.checklist.map((i) => (updates.has(i.id) ? { ...i, completed: Boolean(updates.get(i.id)) } : i));
        qc.setQueryData<Cleaning>(queryKeys.cleanings.detail(id), {
          ...previous,
          checklist,
          checklist_done: checklist.filter((i) => i.completed).length,
        });
      }
      return { previous };
    },
    onError: (_error, _body, context) => {
      if (context?.previous) qc.setQueryData(queryKeys.cleanings.detail(id), context.previous);
    },
    onSuccess: onSaved,
  });
}

export function useInspection(id: string) {
  const onSaved = useOnCleaningSaved();
  return useMutation({
    mutationFn: ({ decision, notes }: { decision: "approve" | "fail"; notes?: string }) =>
      api(`/api/v1/cleanings/${id}/${decision}`, cleaningSchema, { method: "POST", body: notes ? { notes } : {} }),
    onSuccess: onSaved,
  });
}
