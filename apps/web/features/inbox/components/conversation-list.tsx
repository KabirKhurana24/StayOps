"use client";

import type { Conversation } from "@stayops/shared";
import { AlertTriangle, ClipboardList, Lock } from "lucide-react";

import { Avatar } from "@/components/ui/misc";
import { useRecentlyChanged } from "@/features/realtime/realtime-provider";
import { formatRelative, formatStayRange } from "@/lib/format";
import { cn } from "@/lib/utils";

import { isUrgent, previewLine, unreadLabel } from "../model";

function ConversationRow({ c, selected, now, onSelect }: { c: Conversation; selected: boolean; now: Date; onSelect: (id: string) => void }) {
  const name = `${c.guest.first_name} ${c.guest.last_name}`;
  const unread = unreadLabel(c.unread_count);
  const changed = useRecentlyChanged(c.id);
  const done = c.status === "RESOLVED" || c.status === "CLOSED";
  return (
    <li>
      <button
        type="button"
        data-testid="conversation-row"
        data-unread={c.unread_count > 0 || undefined}
        aria-current={selected ? "true" : undefined}
        onClick={() => onSelect(c.id)}
        className={cn(
          "group flex w-full gap-3 border-b px-3.5 py-3 text-left transition-colors duration-150 focus-visible:bg-muted/70 focus-visible:outline-none",
          selected ? "bg-accent-soft/70" : "hover:bg-muted/50",
          changed && !selected && "animate-row-in",
        )}
      >
        <Avatar name={name} size="md" className={cn(done && "opacity-60")} />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline justify-between gap-2">
            <span className={cn("truncate text-[13px]", c.unread_count ? "font-semibold text-foreground" : "font-medium")}>{name}</span>
            <span className={cn("shrink-0 text-[11px] tabular", c.unread_count ? "font-medium text-accent" : "text-subtle-foreground")}>
              {c.last_message_at ? formatRelative(c.last_message_at, now) : ""}
            </span>
          </span>
          <span className="mt-0.5 block truncate text-xs text-muted-foreground">
            {c.property ? c.property.name : "No stay linked"}
            {c.unit ? ` · ${c.unit.name}` : ""}
            {c.reservation && c.property ? ` · ${formatStayRange(c.reservation.check_in, c.reservation.check_out, c.property.timezone)}` : ""}
          </span>
          <span className="mt-1 flex items-center gap-2">
            <span className={cn("min-w-0 flex-1 truncate text-xs", c.unread_count ? "text-foreground" : "text-subtle-foreground")}>
              {c.last_message?.internal ? <Lock className="mr-1 inline size-3 align-[-1px]" aria-hidden /> : null}
              {previewLine(c)}
            </span>
            {isUrgent(c.priority) && !done ? (
              <span className="inline-flex items-center gap-0.5 text-[11px] font-medium text-danger" title={`${c.priority.toLowerCase()} priority`}>
                <AlertTriangle className="size-3" aria-hidden />
                <span className="sr-only">{c.priority} priority</span>
              </span>
            ) : null}
            {c.open_requests ? (
              <span className="inline-flex items-center gap-0.5 text-[11px] text-warning" title={`${c.open_requests} open request(s)`} data-testid="open-requests">
                <ClipboardList className="size-3" aria-hidden />
                {c.open_requests}
              </span>
            ) : null}
            {unread ? (
              <span
                className="grid h-[18px] min-w-[18px] place-items-center rounded-full bg-accent px-1 text-[10px] font-semibold text-white tabular"
                data-testid="unread-badge"
                aria-label={`${c.unread_count} unread`}
              >
                {unread}
              </span>
            ) : done ? (
              <span className="text-[11px] text-subtle-foreground">{c.status === "RESOLVED" ? "Resolved" : "Closed"}</span>
            ) : null}
          </span>
        </span>
      </button>
    </li>
  );
}

export function ConversationList({
  items,
  selectedId,
  onSelect,
  now,
}: {
  items: Conversation[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  now: Date;
}) {
  return (
    <ul aria-label="Conversations" className="min-h-0">
      {items.map((c) => (
        <ConversationRow key={c.id} c={c} selected={c.id === selectedId} now={now} onSelect={onSelect} />
      ))}
    </ul>
  );
}
