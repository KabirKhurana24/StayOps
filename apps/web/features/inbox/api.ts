"use client";

import {
  type Channel,
  channelInfoSchema,
  type Conversation,
  type ConversationPriority,
  type ConversationStatus,
  conversationContextSchema,
  conversationSchema,
  type InboxView,
  inboxCountsSchema,
  messageSchema,
  pageSchema,
  portalLinkIssuedSchema,
  portalLinkStatusSchema,
} from "@stayops/shared";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";

const conversationPage = pageSchema(conversationSchema);

export type InboxFilters = {
  view?: InboxView;
  q?: string;
  guest_id?: string;
  reservation_id?: string;
  channel?: Channel;
  limit?: number;
};

/** Conversation list, newest activity first. Realtime invalidates it on every message. */
export function useConversations(filters: InboxFilters, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    enabled,
    queryKey: queryKeys.inbox.list(filters),
    queryFn: ({ signal }) =>
      api("/api/v1/inbox/conversations", conversationPage, { signal, query: { ...filters, limit: filters.limit ?? 100 } }),
    placeholderData: keepPreviousData,
  });
}

export function useInboxCounts(filters: Omit<InboxFilters, "view" | "limit">) {
  return useQuery({
    queryKey: queryKeys.inbox.counts(filters),
    queryFn: ({ signal }) => api("/api/v1/inbox/counts", inboxCountsSchema, { signal, query: filters }),
    placeholderData: keepPreviousData,
  });
}

/** One conversation — used when the open thread isn't in the current list filter. */
export function useConversation(id: string | null, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    enabled: Boolean(id) && enabled,
    queryKey: queryKeys.inbox.detail(id ?? ""),
    queryFn: ({ signal }) => api(`/api/v1/inbox/conversations/${id}`, conversationSchema, { signal }),
  });
}

export function useConversationContext(id: string | null) {
  return useQuery({
    enabled: Boolean(id),
    queryKey: queryKeys.inbox.context(id ?? ""),
    queryFn: ({ signal }) => api(`/api/v1/inbox/conversations/${id}/context`, conversationContextSchema, { signal }),
  });
}

export function useMessages(conversationId: string | null) {
  return useQuery({
    enabled: Boolean(conversationId),
    queryKey: queryKeys.inbox.messages(conversationId ?? ""),
    queryFn: ({ signal }) => api(`/api/v1/inbox/conversations/${conversationId}/messages`, z.array(messageSchema), { signal }),
  });
}

export function useChannels() {
  return useQuery({
    queryKey: queryKeys.inbox.channels,
    queryFn: ({ signal }) => api("/api/v1/inbox/channels", z.array(channelInfoSchema), { signal }),
    staleTime: 10 * 60_000,
  });
}

function useInboxRefresh() {
  const qc = useQueryClient();
  return (conversation?: Conversation) => {
    if (conversation) qc.setQueryData(queryKeys.inbox.detail(conversation.id), conversation);
    void qc.invalidateQueries({ queryKey: queryKeys.inbox.all });
    void qc.invalidateQueries({ queryKey: queryKeys.audit.all });
  };
}

export function useSendMessage(conversationId: string) {
  const qc = useQueryClient();
  const refresh = useInboxRefresh();
  return useMutation({
    meta: { inlineErrors: true },
    mutationFn: ({ content, internal, idempotencyKey }: { content: string; internal: boolean; idempotencyKey: string }) =>
      api(`/api/v1/inbox/conversations/${conversationId}/messages`, messageSchema, {
        method: "POST",
        body: { content, internal },
        idempotencyKey,
      }),
    onSuccess: (message) => {
      // Show it immediately; the realtime notice then refreshes everything else.
      qc.setQueryData<z.infer<typeof messageSchema>[]>(queryKeys.inbox.messages(conversationId), (prev) =>
        prev && !prev.some((m) => m.id === message.id) ? [...prev, message] : prev,
      );
      refresh();
    },
  });
}

export function useMarkRead(conversationId: string) {
  const refresh = useInboxRefresh();
  return useMutation({
    meta: { inlineErrors: true }, // background bookkeeping: never toast
    mutationFn: () => api(`/api/v1/inbox/conversations/${conversationId}/read`, conversationSchema, { method: "POST" }),
    onSuccess: (c) => refresh(c),
  });
}

export function useUpdateConversation(conversationId: string) {
  const refresh = useInboxRefresh();
  return useMutation({
    mutationFn: (body: { status?: ConversationStatus; priority?: ConversationPriority; expected_version?: number }) =>
      api(`/api/v1/inbox/conversations/${conversationId}`, conversationSchema, { method: "PATCH", body }),
    onSuccess: (c) => refresh(c),
  });
}

export function useAssignConversation(conversationId: string) {
  const refresh = useInboxRefresh();
  return useMutation({
    mutationFn: (assigneeId: string | null) =>
      api(`/api/v1/inbox/conversations/${conversationId}/assign`, conversationSchema, {
        method: "POST",
        body: { assignee_id: assigneeId },
      }),
    onSuccess: (c) => refresh(c),
  });
}

/** Open (or reuse) the stay's in-app conversation — e.g. "Message guest" on a reservation. */
export function useStartConversation() {
  const refresh = useInboxRefresh();
  return useMutation({
    mutationFn: (body: { reservation_id?: string; guest_id?: string; channel?: Channel }) =>
      api("/api/v1/inbox/conversations", conversationSchema, { method: "POST", body }),
    onSuccess: (c) => refresh(c),
  });
}

// ------------------------------------------------------------------ guest portal links

export function usePortalLink(reservationId: string | null | undefined) {
  return useQuery({
    enabled: Boolean(reservationId),
    queryKey: queryKeys.portalLink(reservationId ?? ""),
    queryFn: ({ signal }) => api(`/api/v1/reservations/${reservationId}/portal-link`, portalLinkStatusSchema, { signal }),
  });
}

export function useIssuePortalLink(reservationId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api(`/api/v1/reservations/${reservationId}/portal-link`, portalLinkIssuedSchema, { method: "POST" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.portalLink(reservationId) });
      void qc.invalidateQueries({ queryKey: queryKeys.inbox.all });
    },
  });
}

export function useRevokePortalLink(reservationId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api(`/api/v1/reservations/${reservationId}/portal-link/revoke`, portalLinkStatusSchema, { method: "POST" }),
    onSuccess: (status) => {
      qc.setQueryData(queryKeys.portalLink(reservationId), status);
      void qc.invalidateQueries({ queryKey: queryKeys.inbox.all });
    },
  });
}
