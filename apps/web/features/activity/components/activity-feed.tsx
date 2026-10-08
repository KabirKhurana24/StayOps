"use client";

import type { AuditLog } from "@stayops/shared";
import Link from "next/link";

import { useRecentlyChanged } from "@/features/realtime/realtime-provider";
import { formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";

import { describeActivity, TONE_CLASS } from "../describe";

function ActivityItem({ entry, now }: { entry: AuditLog; now: Date }) {
  const fresh = useRecentlyChanged(entry.id);
  const d = describeActivity(entry);
  const Icon = d.icon;
  const change = d.changes[0];
  const body = (
    <div className="flex gap-3 px-4 py-2.5">
      <span className={cn("mt-0.5 grid size-6 shrink-0 place-items-center rounded-md", TONE_CLASS[d.tone])} aria-hidden>
        <Icon className="size-3.5" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-[13px] leading-snug">
          <span className="font-medium">{d.actor}</span> <span className="text-muted-foreground">{d.verb}</span>
          {d.subject ? <span className="font-medium"> {d.subject}</span> : null}
        </div>
        {change ? (
          <div className="truncate text-xs text-muted-foreground">
            {change.label}: {change.from} → <span className="text-foreground">{change.to}</span>
            {d.changes.length > 1 ? ` +${d.changes.length - 1}` : ""}
          </div>
        ) : d.context ? (
          <div className="truncate text-xs text-muted-foreground">{d.context}</div>
        ) : null}
      </div>
      <time className="shrink-0 pt-px text-[11px] text-subtle-foreground tabular" dateTime={entry.occurred_at} title={new Date(entry.occurred_at).toLocaleString()}>
        {formatRelative(entry.occurred_at, now)}
      </time>
    </div>
  );
  return (
    <li className={cn("transition-colors", fresh && "animate-row-in")} data-testid="activity-item">
      {d.href ? (
        <Link href={d.href} className="block transition-colors hover:bg-muted/45">
          {body}
        </Link>
      ) : (
        body
      )}
    </li>
  );
}

/** Compact recent-activity list (Command Center, detail sidebars). */
export function ActivityFeed({ entries, now }: { entries: AuditLog[]; now: Date }) {
  return (
    <ul className="divide-y">
      {entries.map((entry) => (
        <ActivityItem key={entry.id} entry={entry} now={now} />
      ))}
    </ul>
  );
}
