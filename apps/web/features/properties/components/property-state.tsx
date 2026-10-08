import type { Property } from "@stayops/shared";

import { Badge } from "@/components/ui/badge";

/** One-word operational state, most urgent first. */
export function PropertyStateBadge({ p }: { p: Property }) {
  const s = p.stats;
  if (p.status !== "ACTIVE") return <Badge dot>{p.status === "ARCHIVED" ? "Archived" : "Inactive"}</Badge>;
  if (s?.maintenance_units) return <Badge tone="warning" dot>{s.maintenance_units} in maintenance</Badge>;
  if (s && s.active_units > 0 && s.occupied_units === s.active_units) return <Badge tone="accent" dot>Fully occupied</Badge>;
  if (s?.arrivals_today) return <Badge tone="info" dot>{s.arrivals_today} arriving today</Badge>;
  return <Badge tone="success" dot>Operational</Badge>;
}

/** "14:32 local" for a timezone (re-rendered with the page; no ticking clock). */
export function localClock(tz: string, now = new Date()): string {
  return new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(now);
}

export function cityFromTimezone(tz: string): string {
  return (tz.split("/").pop() ?? tz).replaceAll("_", " ");
}
