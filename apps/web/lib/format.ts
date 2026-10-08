import type { ReservationSource, ReservationStatus } from "@stayops/shared";

export function formatMoney(amount: string | number, currency: string, opts: { compact?: boolean } = {}): string {
  return new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency,
    maximumFractionDigits: opts.compact ? 0 : 2,
    minimumFractionDigits: opts.compact ? 0 : undefined,
  }).format(Number(amount));
}

/** Format an instant in the *property's* timezone. */
export function formatInZone(
  iso: string,
  timeZone: string,
  opts: Intl.DateTimeFormatOptions = { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" },
): string {
  return new Intl.DateTimeFormat("en-GB", { timeZone, hourCycle: "h23", ...opts }).format(new Date(iso));
}

export const formatTime = (iso: string, tz: string) => formatInZone(iso, tz, { hour: "2-digit", minute: "2-digit" });

export const formatDay = (iso: string, tz: string) => formatInZone(iso, tz, { weekday: "short", day: "numeric", month: "short" });

export const formatShortDate = (iso: string, tz: string) => formatInZone(iso, tz, { day: "numeric", month: "short" });

export const formatFull = (iso: string, tz: string) =>
  formatInZone(iso, tz, { weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });

/** "5–8 Oct" / "29 Sep – 2 Oct" in the property's timezone. */
export function formatStayRange(checkIn: string, checkOut: string, tz: string): string {
  const parts = (iso: string) => {
    const p = new Intl.DateTimeFormat("en-GB", { timeZone: tz, day: "numeric", month: "short", year: "numeric" }).formatToParts(new Date(iso));
    const get = (t: string) => p.find((x) => x.type === t)?.value ?? "";
    return { day: get("day"), month: get("month"), year: get("year") };
  };
  const a = parts(checkIn);
  const b = parts(checkOut);
  if (a.month === b.month && a.year === b.year) return `${a.day}–${b.day} ${a.month}`;
  return `${a.day} ${a.month} – ${b.day} ${b.month}`;
}

export function formatRelative(iso: string, now: Date = new Date()): string {
  const diffSec = Math.round((new Date(iso).getTime() - now.getTime()) / 1000);
  const abs = Math.abs(diffSec);
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  if (abs < 45) return "just now";
  if (abs < 3600) return rtf.format(Math.round(diffSec / 60), "minute");
  if (abs < 86_400) return rtf.format(Math.round(diffSec / 3600), "hour");
  if (abs < 86_400 * 30) return rtf.format(Math.round(diffSec / 86_400), "day");
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" }).format(new Date(iso));
}

/** Browser-local clock time for activity feeds ("10:42"). */
export const formatClock = (iso: string) =>
  new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(iso));

/** Day heading for grouped timelines: "Today", "Yesterday", "Mon 3 Oct". */
export function formatDayHeading(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const key = (x: Date) => `${x.getFullYear()}-${x.getMonth()}-${x.getDate()}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (key(d) === key(now)) return "Today";
  if (key(d) === key(yesterday)) return "Yesterday";
  return new Intl.DateTimeFormat("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    ...(d.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}),
  }).format(d);
}

export function greeting(now: Date = new Date()): string {
  const h = now.getHours();
  if (h < 5) return "Good evening";
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

export function firstName(fullName: string | null | undefined, email?: string | null): string {
  const name = fullName?.trim();
  if (name) return name.split(/\s+/)[0] ?? name;
  const local = email?.split("@")[0];
  return local ? local.charAt(0).toUpperCase() + local.slice(1) : "there";
}

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export const STATUS_LABEL: Record<ReservationStatus, string> = {
  PENDING: "Pending",
  CONFIRMED: "Confirmed",
  CHECKED_IN: "Checked in",
  CHECKED_OUT: "Checked out",
  CANCELLED: "Cancelled",
};

export const SOURCE_LABEL: Record<ReservationSource, string> = {
  DIRECT: "Direct",
  AIRBNB: "Airbnb",
  BOOKING_COM: "Booking.com",
  VRBO: "Vrbo",
  EXPEDIA: "Expedia",
  OTHER: "Other",
};

export const guestName = (g: { first_name: string; last_name: string }) => `${g.first_name} ${g.last_name}`;

const ACTION_VERB: Record<string, string> = {
  "reservation.created": "created reservation",
  "reservation.updated": "updated reservation",
  "reservation.cancelled": "cancelled reservation",
  "reservation.checked_in": "checked in",
  "reservation.checked_out": "checked out",
  "property.created": "added property",
  "property.updated": "updated property",
  "unit.created": "added unit",
  "guest.created": "added guest",
  "organization.created": "created organization",
  "unit.blocked": "blocked unit for maintenance",
  "unit.unblocked": "released unit from maintenance",
  "task.created": "created task",
  "task.updated": "updated task",
  "task.assigned": "assigned task",
  "task.started": "started task",
  "task.completed": "completed task",
  "task.cancelled": "cancelled task",
  "task.reopened": "reopened task",
  "cleaning.created": "created cleaning",
  "cleaning.assigned": "assigned cleaning",
  "cleaning.started": "started cleaning",
  "cleaning.updated": "updated checklist",
  "cleaning.completed": "completed cleaning",
  "cleaning.inspection_required": "requested inspection",
  "cleaning.approved": "approved cleaning",
  "cleaning.failed": "failed inspection",
  "maintenance.created": "reported issue",
  "maintenance.updated": "updated work order",
  "maintenance.assigned": "assigned work order",
  "maintenance.started": "started work order",
  "maintenance.blocked": "put work order on hold",
  "maintenance.resolved": "resolved work order",
  "maintenance.closed": "closed work order",
  "guest.updated": "updated guest",
  "conversation.created": "started conversation",
  "conversation.updated": "updated conversation",
  "conversation.assigned": "assigned conversation",
  "message.created": "sent message",
  "message.read": "read messages",
  "guest_request.created": "raised guest request",
  "guest_request.updated": "updated guest request",
  "guest_request.resolved": "resolved guest request",
  "guest_request.closed": "closed guest request",
  "knowledge.created": "added guide entry",
  "knowledge.updated": "updated guide entry",
  "knowledge.archived": "archived guide entry",
  "guest_portal.link_created": "shared guest portal link",
  "guest_portal.link_revoked": "revoked guest portal link",
};

export function humanizeAction(action: string): string {
  return ACTION_VERB[action] ?? action.replaceAll(".", " ").replaceAll("_", " ");
}

/** Activity categories map onto audit entity types. */
export const ACTIVITY_CATEGORIES = {
  all: { label: "All", entityTypes: undefined },
  reservations: { label: "Reservations", entityTypes: ["reservation"] },
  guests: { label: "Guests", entityTypes: ["guest"] },
  inbox: { label: "Inbox", entityTypes: ["conversation", "message", "guest_request", "guest_portal_link"] },
  properties: { label: "Properties", entityTypes: ["property", "unit", "knowledge"] },
  operations: { label: "Operations", entityTypes: ["task", "cleaning", "maintenance"] },
  system: { label: "System", entityTypes: ["organization"] },
} as const satisfies Record<string, { label: string; entityTypes: readonly string[] | undefined }>;
export type ActivityCategory = keyof typeof ACTIVITY_CATEGORIES;

const FIELD_LABEL: Record<string, string> = {
  check_in: "Check-in",
  check_out: "Check-out",
  status: "Status",
  adults: "Adults",
  children: "Children",
  source: "Source",
  total_amount: "Amount",
  currency: "Currency",
  notes: "Notes",
  unit_id: "Unit",
  guest_id: "Guest",
  name: "Name",
  address: "Address",
  timezone: "Timezone",
  default_check_in_time: "Default check-in",
  default_check_out_time: "Default check-out",
  priority: "Priority",
  due_at: "Due",
  title: "Title",
  description: "Description",
  category: "Category",
  blocks_unit: "Blocks unit",
  assigned_to: "Assignee",
};

export const fieldLabel = (field: string) => FIELD_LABEL[field] ?? field.replaceAll("_", " ");

/** Human value for an audited field change. Instants render in the property's timezone. */
export function formatChangeValue(field: string, value: unknown, tz: string | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  if ((field === "check_in" || field === "check_out" || field === "due_at") && typeof value === "string" && tz) {
    return formatInZone(value, tz, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  }
  if (field === "status" && typeof value === "string" && value in STATUS_LABEL) return STATUS_LABEL[value as ReservationStatus];
  if (field === "source" && typeof value === "string" && value in SOURCE_LABEL) return SOURCE_LABEL[value as ReservationSource];
  return String(value);
}
