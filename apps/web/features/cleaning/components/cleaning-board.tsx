"use client";

import type { Cleaning } from "@stayops/shared";
import { AlertTriangle, ArrowRight, Wrench } from "lucide-react";

import { Avatar } from "@/components/ui/misc";
import { Table, TableScroll, Td, Th, THead, Tr } from "@/components/ui/table";
import { CleaningStatusBadge, DueLabel, PriorityLabel } from "@/features/operations/components/badges";
import { formatTurnover } from "@/features/operations/format";
import { useRecentlyChanged } from "@/features/realtime/realtime-provider";
import { formatInZone } from "@/lib/format";
import { cn } from "@/lib/utils";

const time = (iso: string, tz: string) => formatInZone(iso, tz, { hour: "2-digit", minute: "2-digit" });
const day = (iso: string, tz: string) => formatInZone(iso, tz, { weekday: "short", day: "numeric", month: "short" });

/** "Checkout 11:00 → Next check-in 15:00 · 4h turnover", tight windows emphasised. */
export function TurnoverWindow({ cleaning, compact = false }: { cleaning: Cleaning; compact?: boolean }) {
  const t = cleaning.turnover;
  const tz = cleaning.task.property.timezone;
  return (
    <div className="min-w-0" data-testid="turnover-window" data-tight={t.tight ? "true" : "false"} data-minutes={t.minutes ?? ""}>
      <div className="flex items-center gap-1.5 text-[13px] whitespace-nowrap tabular">
        <span>{t.checkout_at ? time(t.checkout_at, tz) : "—"}</span>
        <ArrowRight className="size-3 text-subtle-foreground" aria-hidden />
        <span>{t.next_check_in_at ? time(t.next_check_in_at, tz) : "—"}</span>
      </div>
      <div
        className={cn(
          "inline-flex items-center gap-1 text-xs",
          t.tight ? "font-medium text-danger" : "text-muted-foreground",
        )}
        title={t.tight ? `Below the ${t.threshold_minutes / 60}h tight-turnover threshold` : undefined}
      >
        {t.tight ? <AlertTriangle className="size-3" aria-hidden /> : null}
        {formatTurnover(t.minutes)}
        {!compact && t.next_check_in_at && !t.same_day ? <span className="text-subtle-foreground">· {day(t.next_check_in_at, tz)}</span> : null}
      </div>
    </div>
  );
}

function Row({ c, now, onOpen }: { c: Cleaning; now: number; onOpen: (id: string) => void }) {
  const cleaningChanged = useRecentlyChanged(c.id);
  const taskChanged = useRecentlyChanged(c.task.id);
  const fresh = cleaningChanged || taskChanged;
  const tz = c.task.property.timezone;
  const done = c.status === "APPROVED" || c.task.status === "CANCELLED";
  return (
    <Tr
      interactive
      tabIndex={0}
      onClick={() => onOpen(c.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter") onOpen(c.id);
      }}
      className={cn(fresh && "animate-row-in", c.turnover.tight && !done && "bg-danger/[0.03]")}
      data-testid="cleaning-row"
      data-cleaning-id={c.id}
      data-status={c.status}
      data-tight={c.turnover.tight ? "true" : "false"}
    >
      <Td className="max-w-[220px]">
        <div className="truncate font-medium">
          {c.task.unit ? `Unit ${c.task.unit.name}` : c.task.title}
          {c.work_orders.some((w) => w.status !== "RESOLVED" && w.status !== "CLOSED") ? (
            <Wrench className="ml-1.5 inline size-3 text-warning" aria-label="Open maintenance issue" />
          ) : null}
        </div>
        <div className="truncate text-xs text-muted-foreground">{c.task.property.name}</div>
      </Td>
      <Td className="whitespace-nowrap">
        {c.departure ? (
          <>
            <div className="text-[13px] tabular">{time(c.departure.check_out, tz)}</div>
            <div className="max-w-[140px] truncate text-xs text-muted-foreground">{c.departure.guest_name}</div>
          </>
        ) : (
          <span className="text-xs text-subtle-foreground">—</span>
        )}
      </Td>
      <Td className="whitespace-nowrap">
        {c.next_arrival ? (
          <>
            <div className="text-[13px] tabular">
              {time(c.next_arrival.check_in, tz)}
              {!c.turnover.same_day ? <span className="text-xs text-muted-foreground"> · {day(c.next_arrival.check_in, tz)}</span> : null}
            </div>
            <div className="max-w-[140px] truncate text-xs text-muted-foreground">{c.next_arrival.guest_name}</div>
          </>
        ) : (
          <span className="text-xs text-subtle-foreground">No arrival</span>
        )}
      </Td>
      <Td>
        <TurnoverWindow cleaning={c} compact />
      </Td>
      <Td className="max-w-[160px]">
        {c.task.assignee ? (
          <span className="inline-flex min-w-0 items-center gap-1.5" data-testid="cleaner">
            <Avatar name={c.task.assignee.name} size="xs" />
            <span className="truncate">{c.task.assignee.name}</span>
          </span>
        ) : (
          <span className="text-xs text-subtle-foreground" data-testid="cleaner">
            Unassigned
          </span>
        )}
      </Td>
      <Td className="whitespace-nowrap">
        <CleaningStatusBadge status={c.status} />
        <div className="mt-0.5">
          <DueLabel dueAt={c.task.due_at} now={now} done={done || c.status === "INSPECTION_REQUIRED" || c.status === "COMPLETED"} />
        </div>
      </Td>
      <Td>
        <PriorityLabel priority={c.task.priority} />
      </Td>
    </Tr>
  );
}

export function CleaningBoard({ rows, now, onOpen }: { rows: Cleaning[]; now: number; onOpen: (id: string) => void }) {
  return (
    <TableScroll>
      <Table className="min-w-[980px]">
        <THead>
          <tr>
            <Th>Unit</Th>
            <Th>Checkout</Th>
            <Th>Next check-in</Th>
            <Th>Turnover</Th>
            <Th>Cleaner</Th>
            <Th>Status</Th>
            <Th>Priority</Th>
          </tr>
        </THead>
        <tbody>
          {rows.map((c) => (
            <Row key={c.id} c={c} now={now} onOpen={onOpen} />
          ))}
        </tbody>
      </Table>
    </TableScroll>
  );
}
