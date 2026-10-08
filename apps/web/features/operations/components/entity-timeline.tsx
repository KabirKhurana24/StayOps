"use client";

import type { AuditLog } from "@stayops/shared";
import { History } from "lucide-react";

import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { useAudit } from "@/features/activity/api";
import { describeActivity, TONE_CLASS } from "@/features/activity/describe";
import { formatInZone } from "@/lib/format";
import { cn } from "@/lib/utils";

function Entry({ entry, tz }: { entry: AuditLog; tz: string }) {
  const d = describeActivity(entry);
  const Icon = d.icon;
  const note = [entry.metadata.reason, entry.metadata.notes, entry.metadata.resolution].find(
    (v): v is string => typeof v === "string" && v.length > 0,
  );
  return (
    <li className="group relative grid grid-cols-[28px_minmax(0,1fr)] gap-x-3 pb-4 last:pb-0" data-testid="timeline-entry" data-action={entry.action}>
      <div className="relative flex justify-center">
        <span aria-hidden className="absolute top-7 -bottom-0 w-px bg-border group-last:hidden" />
        <span className={cn("relative grid size-7 place-items-center rounded-md", TONE_CLASS[d.tone])} aria-hidden>
          <Icon className="size-3.5" />
        </span>
      </div>
      <div className="min-w-0 pt-0.5">
        <div className="text-[13px]">
          <span className="font-medium">{d.actor}</span> <span className="text-muted-foreground">{d.verb}</span>
        </div>
        <div className="text-xs text-muted-foreground">
          <time dateTime={entry.occurred_at} title={`${tz} · request ${entry.request_id ?? "n/a"}`}>
            {formatInZone(entry.occurred_at, tz, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
          </time>
          {d.changes.length ? <> · {d.changes.map((c) => `${c.label}: ${c.from} → ${c.to}`).join(" · ")}</> : null}
        </div>
        {note ? <p className="mt-1 rounded-md border bg-surface-raised px-2.5 py-1.5 text-xs">{note}</p> : null}
      </div>
    </li>
  );
}

/**
 * Audit history for one or more related entities (e.g. a task and its cleaning),
 * merged newest-first. Times render in the property's timezone.
 */
export function EntityTimeline({ entityIds, tz }: { entityIds: (string | null | undefined)[]; tz: string }) {
  const [first, second] = entityIds.filter((id): id is string => Boolean(id));
  const a = useAudit({ entity_id: first, limit: 100 }, { enabled: Boolean(first) });
  const b = useAudit({ entity_id: second, limit: 100 }, { enabled: Boolean(second) });
  if (a.isPending || (second && b.isPending)) return <SkeletonRows rows={3} columns={1} />;
  if (a.error) return <ErrorState compact error={a.error} onRetry={() => a.refetch()} />;
  const entries = [...(a.data?.items ?? []), ...(second ? (b.data?.items ?? []) : [])].sort(
    (x, y) => Date.parse(y.occurred_at) - Date.parse(x.occurred_at),
  );
  if (!entries.length) return <EmptyState compact icon={History} title="No history yet" />;
  return (
    <ol aria-label="Timeline">
      {entries.map((e) => (
        <Entry key={e.id} entry={e} tz={tz} />
      ))}
    </ol>
  );
}
