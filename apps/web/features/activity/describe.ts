import type { AuditLog } from "@stayops/shared";
import {
  Ban,
  BadgeCheck,
  Building2,
  CalendarPlus,
  CircleCheck,
  CircleX,
  ClipboardList,
  DoorClosed,
  DoorOpen,
  Home,
  ListChecks,
  Lock,
  LockOpen,
  type LucideIcon,
  Pause,
  Pencil,
  Play,
  RotateCcw,
  Sparkles,
  UserCheck,
  UserPlus,
  Wrench,
} from "lucide-react";

import { fieldLabel, formatChangeValue, formatStayRange, humanizeAction } from "@/lib/format";

export type ActivityChange = { field: string; label: string; from: string; to: string };

export type ActivityDescription = {
  actor: string;
  verb: string;
  /** Primary object label, e.g. "SO-4KQ7PZ" or "Canal View Apartments". */
  subject: string | null;
  /** Secondary context line, e.g. "Canal View · 101 · Sarah Johnson · 5–8 Oct". */
  context: string | null;
  changes: ActivityChange[];
  href: string | null;
  icon: LucideIcon;
  tone: "accent" | "success" | "danger" | "neutral" | "info";
};

const ICONS: Record<string, { icon: LucideIcon; tone: ActivityDescription["tone"] }> = {
  "reservation.created": { icon: CalendarPlus, tone: "accent" },
  "reservation.updated": { icon: Pencil, tone: "neutral" },
  "reservation.cancelled": { icon: Ban, tone: "danger" },
  "reservation.checked_in": { icon: DoorOpen, tone: "success" },
  "reservation.checked_out": { icon: DoorClosed, tone: "neutral" },
  "property.created": { icon: Building2, tone: "accent" },
  "property.updated": { icon: Pencil, tone: "neutral" },
  "unit.created": { icon: Home, tone: "accent" },
  "guest.created": { icon: UserPlus, tone: "info" },
  "organization.created": { icon: Sparkles, tone: "accent" },
  "unit.blocked": { icon: Lock, tone: "danger" },
  "unit.unblocked": { icon: LockOpen, tone: "success" },
  "task.created": { icon: ClipboardList, tone: "accent" },
  "task.updated": { icon: Pencil, tone: "neutral" },
  "task.assigned": { icon: UserCheck, tone: "info" },
  "task.started": { icon: Play, tone: "info" },
  "task.completed": { icon: CircleCheck, tone: "success" },
  "task.cancelled": { icon: Ban, tone: "danger" },
  "task.reopened": { icon: RotateCcw, tone: "danger" },
  "cleaning.created": { icon: Sparkles, tone: "accent" },
  "cleaning.assigned": { icon: UserCheck, tone: "info" },
  "cleaning.started": { icon: Play, tone: "info" },
  "cleaning.updated": { icon: ListChecks, tone: "neutral" },
  "cleaning.completed": { icon: CircleCheck, tone: "success" },
  "cleaning.inspection_required": { icon: ClipboardList, tone: "info" },
  "cleaning.approved": { icon: BadgeCheck, tone: "success" },
  "cleaning.failed": { icon: CircleX, tone: "danger" },
  "maintenance.created": { icon: Wrench, tone: "danger" },
  "maintenance.updated": { icon: Pencil, tone: "neutral" },
  "maintenance.assigned": { icon: UserCheck, tone: "info" },
  "maintenance.started": { icon: Play, tone: "info" },
  "maintenance.blocked": { icon: Pause, tone: "danger" },
  "maintenance.resolved": { icon: CircleCheck, tone: "success" },
  "maintenance.closed": { icon: BadgeCheck, tone: "neutral" },
};

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

function entityHref(entry: AuditLog): string | null {
  switch (entry.entity_type) {
    case "reservation":
      return `/reservations?r=${entry.entity_id}`;
    case "property":
      return `/properties/${entry.entity_id}`;
    case "guest":
      return `/guests/${entry.entity_id}`;
    case "unit": {
      const pid = str(entry.metadata.property_id);
      return pid ? `/properties/${pid}` : null;
    }
    case "task":
      return `/tasks?t=${entry.entity_id}`;
    case "cleaning":
      return `/cleaning?c=${entry.entity_id}`;
    case "maintenance":
      return `/maintenance?wo=${entry.entity_id}`;
    default:
      return null;
  }
}

/** Turns an audit entry into a readable sentence plus context, for any feed. */
export function describeActivity(entry: AuditLog): ActivityDescription {
  const m = entry.metadata;
  const tz = str(m.timezone) ?? undefined;
  const rawChanges = (m.changes ?? {}) as Record<string, { from: unknown; to: unknown }>;
  const changes: ActivityChange[] = Object.entries(rawChanges)
    .filter(([field]) => !field.endsWith("_id"))
    .map(([field, c]) => ({
      field,
      label: fieldLabel(field),
      from: formatChangeValue(field, c.from, tz),
      to: formatChangeValue(field, c.to, tz),
    }));

  let subject: string | null = null;
  const context: (string | null)[] = [];
  if (entry.entity_type === "reservation") {
    subject = str(m.confirmation_code);
    context.push(str(m.property), str(m.unit) ? `Unit ${str(m.unit)}` : null, str(m.guest));
    const ci = str(m.check_in);
    const co = str(m.check_out);
    if (ci && co) context.push(formatStayRange(ci, co, tz ?? "UTC"));
  } else if (entry.entity_type === "unit") {
    subject = str(m.name) ? `Unit ${str(m.name)}` : null;
    context.push(str(m.property_name), str(m.work_order));
  } else if (entry.entity_type === "task" || entry.entity_type === "cleaning" || entry.entity_type === "maintenance") {
    subject = str(m.title);
    context.push(str(m.property), str(m.unit) ? `Unit ${str(m.unit)}` : null, str(m.assignee) ? `→ ${str(m.assignee)}` : null);
  } else {
    subject = str(m.name);
    if (entry.entity_type === "property" && !changes.length) context.push(str(m.timezone));
  }

  const style = ICONS[entry.action] ?? { icon: Pencil, tone: "neutral" as const };
  const actor = entry.actor_label ?? (entry.actor_type === "SYSTEM" ? "System" : entry.actor_type.toLowerCase());
  return {
    actor,
    verb: humanizeAction(entry.action),
    subject,
    context: context.filter(Boolean).join(" · ") || null,
    changes,
    href: entityHref(entry),
    icon: style.icon,
    tone: style.tone,
  };
}

export const TONE_CLASS: Record<ActivityDescription["tone"], string> = {
  accent: "bg-accent-soft text-accent",
  success: "bg-success/10 text-success",
  danger: "bg-danger/8 text-danger",
  info: "bg-info/10 text-info",
  neutral: "bg-muted text-muted-foreground",
};
