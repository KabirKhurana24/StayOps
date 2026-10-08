"use client";

import type { AuditLog } from "@stayops/shared";
import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { useRecentlyChanged } from "@/features/realtime/realtime-provider";
import { formatClock, formatDayHeading } from "@/lib/format";
import { cn } from "@/lib/utils";

import { describeActivity, TONE_CLASS } from "../describe";

function TimelineItem({ entry }: { entry: AuditLog }) {
  const [open, setOpen] = useState(false);
  const fresh = useRecentlyChanged(entry.id);
  const d = describeActivity(entry);
  const Icon = d.icon;
  const hasDetails = d.changes.length > 0 || Boolean(entry.request_id);

  return (
    <li className={cn("group relative grid grid-cols-[52px_28px_minmax(0,1fr)] gap-x-3 py-2.5", fresh && "animate-row-in rounded-md")} data-testid="activity-item">
      <time dateTime={entry.occurred_at} className="pt-1 text-right text-xs text-subtle-foreground tabular" title={new Date(entry.occurred_at).toLocaleString()}>
        {formatClock(entry.occurred_at)}
      </time>
      <div className="relative flex justify-center">
        <span aria-hidden className="absolute top-0 -bottom-2.5 w-px bg-border group-last:hidden" />
        <span className={cn("relative z-[1] grid size-7 place-items-center rounded-md ring-4 ring-surface", TONE_CLASS[d.tone])} aria-hidden>
          <Icon className="size-3.5" />
        </span>
      </div>
      <div className="min-w-0 pt-0.5">
        <div className="text-[13px] leading-snug">
          <span className="font-medium">{d.actor}</span> <span className="text-muted-foreground">{d.verb}</span>{" "}
          {d.subject ? (
            d.href ? (
              <Link href={d.href} className="font-medium hover:underline">
                {d.subject}
              </Link>
            ) : (
              <span className="font-medium">{d.subject}</span>
            )
          ) : null}
        </div>
        {d.context ? <div className="mt-0.5 text-xs text-muted-foreground">{d.context}</div> : null}
        {d.changes.length ? (
          <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
            {(open ? d.changes : d.changes.slice(0, 2)).map((c) => (
              <li key={c.field}>
                {c.label} <span className="line-through decoration-subtle-foreground/60">{c.from}</span> →{" "}
                <span className="font-medium text-foreground">{c.to}</span>
              </li>
            ))}
          </ul>
        ) : null}
        {hasDetails ? (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="mt-1 inline-flex items-center gap-0.5 text-[11px] text-subtle-foreground hover:text-foreground"
          >
            <ChevronRight className={cn("size-3 transition-transform", open && "rotate-90")} />
            {open ? "Hide details" : d.changes.length > 2 ? `${d.changes.length - 2} more · details` : "Details"}
          </button>
        ) : null}
        {open ? (
          <dl className="mt-1.5 grid max-w-md grid-cols-[88px_minmax(0,1fr)] gap-x-3 gap-y-0.5 rounded-md border bg-surface-raised px-3 py-2 font-mono text-[11px] text-muted-foreground">
            <dt>Action</dt>
            <dd className="truncate">{entry.action}</dd>
            <dt>Entity</dt>
            <dd className="truncate">
              {entry.entity_type}/{entry.entity_id.slice(0, 8)}
            </dd>
            <dt>Actor</dt>
            <dd className="truncate">{entry.actor_type.toLowerCase()}</dd>
            {entry.request_id ? (
              <>
                <dt>Request</dt>
                <dd className="truncate">{entry.request_id}</dd>
              </>
            ) : null}
            {entry.event_id ? (
              <>
                <dt>Event</dt>
                <dd className="truncate">{entry.event_id}</dd>
              </>
            ) : null}
          </dl>
        ) : null}
      </div>
    </li>
  );
}

/** Audit entries grouped by (browser-local) day, newest first. */
export function ActivityTimeline({ entries }: { entries: AuditLog[] }) {
  const now = new Date();
  const groups: { heading: string; items: AuditLog[] }[] = [];
  for (const entry of entries) {
    const heading = formatDayHeading(entry.occurred_at, now);
    const last = groups[groups.length - 1];
    if (last && last.heading === heading) last.items.push(entry);
    else groups.push({ heading, items: [entry] });
  }
  return (
    <div className="divide-y">
      {groups.map((g) => (
        <section key={g.heading} aria-label={g.heading} className="px-2 py-2 sm:px-4">
          <h3 className="sticky top-12 z-[2] -mx-2 bg-surface/95 px-2 py-1.5 text-xs font-semibold backdrop-blur sm:-mx-4 sm:px-4">
            {g.heading} <span className="font-normal text-subtle-foreground">· {g.items.length}</span>
          </h3>
          <ol>
            {g.items.map((e) => (
              <TimelineItem key={e.id} entry={e} />
            ))}
          </ol>
        </section>
      ))}
    </div>
  );
}
