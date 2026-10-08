/**
 * Pure inbox logic shared by the screens and unit-tested without a browser:
 * list previews, unread badges, thread grouping, request actions and which pane a
 * narrow screen shows.
 */
import type {
  Conversation,
  ConversationPriority,
  GuestRequest,
  GuestRequestStatus,
  InboxView,
  Message,
  StayPhase,
} from "@stayops/shared";

export const INBOX_VIEW_LABEL: Record<InboxView, string> = {
  all: "All",
  unread: "Unread",
  open: "Open",
  mine: "Assigned to me",
  high: "High priority",
  resolved: "Resolved",
};

export const PHASE_LABEL: Record<StayPhase, string> = {
  UPCOMING: "Upcoming",
  ARRIVING_TODAY: "Arriving today",
  IN_HOUSE: "In house",
  DEPARTING_TODAY: "Departing today",
  DEPARTED: "Departed",
};

/** "You: …" / "Note: …" / plain guest text — what the list row shows under the name. */
export function previewLine(c: Pick<Conversation, "last_message">): string {
  const m = c.last_message;
  if (!m) return "No messages yet";
  if (m.internal) return `Note: ${m.content}`;
  if (m.sender_type === "STAFF") return `You: ${m.content}`;
  if (m.sender_type === "AI") return `Assistant: ${m.content}`;
  return m.content;
}

export const unreadLabel = (count: number): string | null => (count <= 0 ? null : count > 99 ? "99+" : String(count));

export const isUrgent = (p: ConversationPriority) => p === "HIGH" || p === "URGENT";

/** Thread row layout: guest on the left, team/assistant on the right, StayOps centred. */
export function messageSide(m: Pick<Message, "sender_type" | "message_type">): "left" | "right" | "center" {
  if (m.sender_type === "SYSTEM") return "center";
  return m.sender_type === "GUEST" ? "left" : "right";
}

export type ThreadDay = { key: string; label: string; messages: Message[] };

/** Groups messages (already in seq order) under local-day headings. */
export function groupByDay(messages: Message[], now: Date, timeZone?: string): ThreadDay[] {
  const fmt = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" });
  const today = fmt.format(now);
  const yesterday = fmt.format(new Date(now.getTime() - 86_400_000));
  const days: ThreadDay[] = [];
  for (const m of messages) {
    const key = fmt.format(new Date(m.created_at));
    let day = days.at(-1);
    if (!day || day.key !== key) {
      const label =
        key === today
          ? "Today"
          : key === yesterday
            ? "Yesterday"
            : new Intl.DateTimeFormat("en-GB", { timeZone, weekday: "short", day: "numeric", month: "short" }).format(new Date(m.created_at));
      day = { key, label, messages: [] };
      days.push(day);
    }
    day.messages.push(m);
  }
  return days;
}

/** Which request actions make sense now (the server state machine has the last word). */
export function requestActions(r: Pick<GuestRequest, "status" | "task" | "work_order">): {
  acknowledge: boolean;
  toTask: boolean;
  toMaintenance: boolean;
  resolve: boolean;
  close: boolean;
  cancel: boolean;
} {
  const s: GuestRequestStatus = r.status;
  const live = s === "OPEN" || s === "ACKNOWLEDGED" || s === "IN_PROGRESS";
  return {
    acknowledge: s === "OPEN",
    toTask: live && !r.task,
    toMaintenance: live && !r.work_order,
    resolve: live,
    close: s === "RESOLVED",
    cancel: live,
  };
}

export const REQUEST_STATUS_TONE: Record<GuestRequestStatus, "info" | "warning" | "accent" | "success" | "neutral"> = {
  OPEN: "warning",
  ACKNOWLEDGED: "info",
  IN_PROGRESS: "accent",
  RESOLVED: "success",
  CLOSED: "neutral",
  CANCELLED: "neutral",
};

export const REQUEST_STATUS_LABEL: Record<GuestRequestStatus, string> = {
  OPEN: "Open",
  ACKNOWLEDGED: "Acknowledged",
  IN_PROGRESS: "In progress",
  RESOLVED: "Resolved",
  CLOSED: "Closed",
  CANCELLED: "Cancelled",
};

/**
 * Narrow screens show one pane at a time: the list until a conversation is open,
 * then the thread (context opens as a sheet). Wide screens show list + thread, and
 * the context panel when there is room.
 */
export function visiblePanes(selected: boolean, width: "narrow" | "medium" | "wide"): { list: boolean; thread: boolean; context: boolean } {
  if (width === "narrow") return { list: !selected, thread: selected, context: false };
  if (width === "medium") return { list: true, thread: true, context: false };
  return { list: true, thread: true, context: selected };
}

/** Portal URL for a freshly issued link: the token rides in the fragment (never sent to servers). */
export const portalUrl = (origin: string, path: string) => `${origin.replace(/\/$/, "")}${path}`;
