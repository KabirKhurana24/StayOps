"use client";

import type { AuditLog } from "@stayops/shared";
import { History } from "lucide-react";

import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { useAudit } from "@/features/activity/api";
import { describeActivity, TONE_CLASS } from "@/features/activity/describe";
import { fieldLabel, formatChangeValue, formatInZone } from "@/lib/format";
import { cn } from "@/lib/utils";

const LIFECYCLE_TITLE: Record<string, string> = {
  "reservation.created": "Reservation created",
  "reservation.cancelled": "Reservation cancelled",
  "reservation.checked_in": "Guest checked in",
  "reservation.checked_out": "Guest checked out",
};

function titleFor(entry: AuditLog): string {
  if (LIFECYCLE_TITLE[entry.action]) return LIFECYCLE_TITLE[entry.action];
  const changes = Object.keys((entry.metadata.changes ?? {}) as object).filter((f) => !f.endsWith("_id"));
  if (changes.length === 1 && changes[0] === "status") return "Status changed";
  if (changes.length === 1) return `${fieldLabel(changes[0] ?? "")} changed`;
  return "Reservation modified";
}

function Entry({ entry, tz }: { entry: AuditLog; tz: string }) {
  const d = describeActivity(entry);
  const Icon = d.icon;
  const changes = Object.entries((entry.metadata.changes ?? {}) as Record<string, { from: unknown; to: unknown }>).filter(
    ([f]) => !f.endsWith("_id") || f === "unit_id",
  );
  return (
    <li className="group relative grid grid-cols-[28px_minmax(0,1fr)] gap-x-3 pb-4 last:pb-0" data-testid="timeline-entry">
      <div className="relative flex justify-center">
        <span aria-hidden className="absolute top-7 -bottom-0 w-px bg-border group-last:hidden" />
        <span className={cn("relative grid size-7 place-items-center rounded-md", TONE_CLASS[d.tone])} aria-hidden>
          <Icon className="size-3.5" />
        </span>
      </div>
      <div className="min-w-0 pt-0.5">
        <div className="text-[13px] font-medium">{titleFor(entry)}</div>
        <div className="text-xs text-muted-foreground">
          {d.actor} {d.verb} ·{" "}
          <time dateTime={entry.occurred_at} title={`${tz} · request ${entry.request_id ?? "n/a"}`}>
            {formatInZone(entry.occurred_at, tz, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
          </time>
        </div>
        {changes.length ? (
          <dl className="mt-1.5 space-y-1 rounded-md border bg-surface-raised px-2.5 py-2 text-xs">
            {changes.map(([field, c]) => (
              <div key={field} className="flex flex-wrap gap-x-1.5">
                <dt className="text-muted-foreground">{field === "unit_id" ? "Unit" : fieldLabel(field)}</dt>
                <dd>
                  {field === "unit_id" ? (
                    <span className="font-medium">moved</span>
                  ) : (
                    <>
                      <span className="text-muted-foreground line-through decoration-subtle-foreground/60">{formatChangeValue(field, c.from, tz)}</span>{" "}
                      → <span className="font-medium">{formatChangeValue(field, c.to, tz)}</span>
                    </>
                  )}
                </dd>
              </div>
            ))}
          </dl>
        ) : null}
      </div>
    </li>
  );
}

/** Lifecycle history of one reservation, from the audit log (times in property timezone). */
export function ReservationTimelinePanel({ reservationId, tz }: { reservationId: string; tz: string }) {
  const audit = useAudit({ entity_type: "reservation", entity_id: reservationId, limit: 100 });
  if (audit.isPending) return <SkeletonRows rows={3} columns={1} />;
  if (audit.error) return <ErrorState compact error={audit.error} onRetry={() => audit.refetch()} />;
  if (!audit.data.items.length) return <EmptyState compact icon={History} title="No history yet" />;
  return (
    <ol aria-label="Reservation timeline">
      {audit.data.items.map((e) => (
        <Entry key={e.id} entry={e} tz={tz} />
      ))}
    </ol>
  );
}
