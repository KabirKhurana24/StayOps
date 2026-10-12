"use client";

import {
  guestAssistantSchema,
  type GuestRequestType,
  guestKnowledgeSchema,
  guestMessageSchema,
  guestRequestViewSchema,
  guestStaySchema,
} from "@stayops/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";

import { guestApi } from "@/lib/api/guest-client";

/**
 * Guests aren't StayOps users and must never join an organization's realtime
 * channel, so the portal polls its own (small, stay-scoped) endpoints instead:
 * messages every few seconds while the page is visible, the rest less often.
 */
const keys = {
  stay: ["guest", "stay"] as const,
  knowledge: ["guest", "knowledge"] as const,
  messages: ["guest", "messages"] as const,
  requests: ["guest", "requests"] as const,
  assistant: ["guest", "assistant"] as const,
};

/** Assistant on/off and the latest run's live progress (polled fast only while it works). */
export function useGuestAssistant() {
  return useQuery({
    queryKey: keys.assistant,
    queryFn: ({ signal }) => guestApi("/assistant", guestAssistantSchema, { signal }),
    refetchInterval: (q) => (q.state.data?.running ? 1_200 : 15_000),
  });
}

export function useGuestStay() {
  return useQuery({ queryKey: keys.stay, queryFn: ({ signal }) => guestApi("/stay", guestStaySchema, { signal }), refetchInterval: 30_000 });
}

export function useGuestKnowledge() {
  return useQuery({ queryKey: keys.knowledge, queryFn: ({ signal }) => guestApi("/knowledge", guestKnowledgeSchema, { signal }), staleTime: 60_000 });
}

export function useGuestMessages() {
  return useQuery({
    queryKey: keys.messages,
    queryFn: ({ signal }) => guestApi("/messages", z.array(guestMessageSchema), { signal }),
    refetchInterval: 4_000,
  });
}

export function useSendGuestMessage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ content, idempotencyKey }: { content: string; idempotencyKey: string }) =>
      guestApi("/messages", guestMessageSchema, { method: "POST", body: { content }, idempotencyKey }),
    onSuccess: (m) => {
      qc.setQueryData<z.infer<typeof guestMessageSchema>[]>(keys.messages, (prev) => (prev && !prev.some((x) => x.id === m.id) ? [...prev, m] : prev));
      void qc.invalidateQueries({ queryKey: keys.messages });
      void qc.invalidateQueries({ queryKey: keys.stay });
      void qc.invalidateQueries({ queryKey: keys.assistant });
    },
  });
}

export function useMarkGuestRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => guestApi("/messages/read", z.null(), { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.stay }),
  });
}

export function useGuestRequests() {
  return useQuery({
    queryKey: keys.requests,
    queryFn: ({ signal }) => guestApi("/requests", z.array(guestRequestViewSchema), { signal }),
    refetchInterval: 15_000,
  });
}

export function useCreateGuestRequestAsGuest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ idempotencyKey, ...body }: { type: GuestRequestType; description: string; requested_for?: string; idempotencyKey: string }) =>
      guestApi("/requests", guestRequestViewSchema, { method: "POST", body, idempotencyKey }),
    onSuccess: () => {
      for (const key of Object.values(keys)) void qc.invalidateQueries({ queryKey: key });
    },
  });
}

export function useCancelGuestRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => guestApi(`/requests/${id}/cancel`, guestRequestViewSchema, { method: "POST" }),
    onSuccess: () => {
      for (const key of Object.values(keys)) void qc.invalidateQueries({ queryKey: key });
    },
  });
}
