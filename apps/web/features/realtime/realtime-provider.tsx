"use client";

import { type BroadcastTable, broadcastPayloadSchema } from "@stayops/shared";
import type { RealtimeChannel } from "@supabase/supabase-js";
import { type QueryKey, useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useContext, useEffect, useRef, useState } from "react";

import { queryKeys } from "@/lib/api/query-keys";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

export type RealtimeStatus = "connecting" | "live" | "offline";

type RealtimeState = {
  status: RealtimeStatus;
  /** entity id → ms timestamp of the last pushed change (drives row highlights). */
  recentlyChanged: ReadonlyMap<string, number>;
  lastChangeAt: number | null;
};

const RealtimeContext = createContext<RealtimeState>({
  status: "connecting",
  recentlyChanged: new Map(),
  lastChangeAt: null,
});

/** Which cached queries a change in each table makes stale. */
// Only *active* queries refetch on invalidation, so broad prefixes stay cheap.
const INVALIDATES: Record<BroadcastTable, QueryKey[]> = {
  reservations: [
    queryKeys.reservations.all,
    queryKeys.calendar.all,
    queryKeys.commandCenter,
    queryKeys.properties.all, // occupancy / next arrival stats
    queryKeys.guests.all, // stay history stats
  ],
  properties: [queryKeys.properties.all, queryKeys.calendar.all, queryKeys.commandCenter],
  units: [queryKeys.units.all, queryKeys.properties.all, queryKeys.calendar.all, queryKeys.commandCenter],
  guests: [queryKeys.guests.all, queryKeys.reservations.all, queryKeys.calendar.all],
  events: [queryKeys.events.all],
  audit_logs: [queryKeys.audit.all, queryKeys.commandCenter],
  // Operations: a task change moves boards, workload, unit state and the Command Center —
  // and the inbox context panel's "work" list and any linked guest request.
  tasks: [
    queryKeys.tasks.all,
    queryKeys.cleanings.all,
    queryKeys.operations.all,
    queryKeys.units.all,
    queryKeys.commandCenter,
    queryKeys.inbox.all,
    queryKeys.guestRequests.all,
  ],
  cleanings: [queryKeys.cleanings.all, queryKeys.tasks.all, queryKeys.operations.all, queryKeys.units.all, queryKeys.commandCenter],
  // Maintenance can block a unit: availability (under reservations), calendar and property views follow.
  maintenance_work_orders: [
    queryKeys.maintenance.all,
    queryKeys.cleanings.all,
    queryKeys.operations.all,
    queryKeys.units.all,
    queryKeys.properties.all,
    queryKeys.reservations.all,
    queryKeys.calendar.all,
    queryKeys.commandCenter,
    queryKeys.inbox.all,
    queryKeys.guestRequests.all,
  ],
  // Inbox: ordering, unread counts and open threads follow every message.
  conversations: [queryKeys.inbox.all, queryKeys.commandCenter],
  messages: [queryKeys.inbox.all, queryKeys.commandCenter],
  guest_requests: [queryKeys.guestRequests.all, queryKeys.inbox.all, queryKeys.commandCenter],
  property_knowledge: [queryKeys.knowledge.all],
};

const FLUSH_MS = 120; // one mutation fans out to several tables; batch the refetches
const HIGHLIGHT_MS = 4_000;

/**
 * Subscribes to the organization's *private* broadcast topic. Database triggers
 * publish a minimal change notice after each committed write; we invalidate the
 * affected queries and TanStack Query refetches through the authorized API. The
 * database stays the source of truth — realtime only says "this changed".
 */
export function RealtimeProvider({ topic, children }: { topic: string | undefined; children: ReactNode }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<RealtimeStatus>("connecting");
  const [recentlyChanged, setRecentlyChanged] = useState<ReadonlyMap<string, number>>(new Map());
  const [lastChangeAt, setLastChangeAt] = useState<number | null>(null);
  const hasConnected = useRef(false);

  useEffect(() => {
    if (!topic) return;
    const supabase = getSupabaseBrowserClient();
    const pending = new Map<string, QueryKey>();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let channel: RealtimeChannel | undefined;
    let disposed = false;
    const expiries: ReturnType<typeof setTimeout>[] = [];

    const flush = () => {
      timer = undefined;
      for (const key of pending.values()) void queryClient.invalidateQueries({ queryKey: key });
      pending.clear();
    };

    const onChange = (raw: unknown) => {
      const parsed = broadcastPayloadSchema.safeParse(raw);
      if (!parsed.success) return;
      const change = parsed.data;
      for (const key of INVALIDATES[change.table]) pending.set(JSON.stringify(key), key);
      const detail =
        change.table === "reservations"
          ? queryKeys.reservations.detail(change.id)
          : change.table === "tasks"
            ? queryKeys.tasks.detail(change.id)
            : change.table === "cleanings"
              ? queryKeys.cleanings.detail(change.id)
              : change.table === "maintenance_work_orders"
                ? queryKeys.maintenance.detail(change.id)
                : null;
      if (detail) pending.set(JSON.stringify(detail), detail);
      // An open thread refreshes from its own notices (the id rides on the payload).
      if (change.conversation_id) {
        const thread = queryKeys.inbox.messages(change.conversation_id);
        pending.set(JSON.stringify(thread), thread);
      }
      timer ??= setTimeout(flush, FLUSH_MS);

      const ids = [change.id, change.entity_id, change.aggregate_id].filter(
        (id): id is string => Boolean(id),
      );
      const stamp = Date.now();
      setLastChangeAt(stamp);
      setRecentlyChanged((prev) => {
        const next = new Map(prev);
        for (const id of ids) next.set(id, stamp);
        return next;
      });
      // Expire the highlight; a newer change to the same id keeps its own stamp.
      expiries.push(
        setTimeout(() => {
          setRecentlyChanged((prev) => {
            const next = new Map(prev);
            for (const id of ids) if (next.get(id) === stamp) next.delete(id);
            return next;
          });
        }, HIGHLIGHT_MS),
      );
    };

    const connect = async () => {
      const { data } = await supabase.auth.getSession();
      if (disposed) return;
      // Private channels are authorized by RLS on realtime.messages using this JWT.
      await supabase.realtime.setAuth(data.session?.access_token ?? null);
      channel = supabase
        .channel(topic, { config: { private: true } })
        .on("broadcast", { event: "*" }, ({ payload }) => onChange(payload))
        .subscribe((state) => {
          if (state === "SUBSCRIBED") {
            setStatus("live");
            // After a reconnect we may have missed changes: refetch everything once.
            if (hasConnected.current) void queryClient.invalidateQueries();
            hasConnected.current = true;
          } else if (state === "CHANNEL_ERROR" || state === "TIMED_OUT" || state === "CLOSED") {
            setStatus("offline");
          }
        });
    };
    void connect();

    // Keep the realtime socket's JWT in step with session refreshes.
    const { data: authListener } = supabase.auth.onAuthStateChange((_event, session) => {
      if (session) void supabase.realtime.setAuth(session.access_token);
    });

    return () => {
      disposed = true;
      authListener.subscription.unsubscribe();
      if (timer) clearTimeout(timer);
      expiries.forEach(clearTimeout);
      if (channel) void supabase.removeChannel(channel);
    };
  }, [topic, queryClient]);

  return (
    <RealtimeContext.Provider value={{ status, recentlyChanged, lastChangeAt }}>
      {children}
    </RealtimeContext.Provider>
  );
}

export const useRealtime = () => useContext(RealtimeContext);

/** True for a few seconds after a realtime change touched `id`. */
export function useRecentlyChanged(id: string | undefined): boolean {
  const { recentlyChanged } = useRealtime();
  return id ? recentlyChanged.has(id) : false;
}
