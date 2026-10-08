"use client";

import type { CommandCenter, Reservation } from "@stayops/shared";
import { CalendarCheck2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Panel } from "@/components/ui/panel";
import { Tabs } from "@/components/ui/segmented";
import { EmptyState } from "@/components/ui/states";
import { Avatar } from "@/components/ui/misc";
import { useRecentlyChanged } from "@/features/realtime/realtime-provider";
import { StatusActions } from "@/features/reservations/components/status-actions";
import { StatusBadge } from "@/features/reservations/components/status-badge";
import { formatInZone, formatShortDate, formatTime, guestName, plural } from "@/lib/format";
import { cn } from "@/lib/utils";

type Kind = "arrivals" | "departures" | "in_house";

function OperationRow({ r, kind, canWrite }: { r: Reservation; kind: Kind; canWrite: boolean }) {
  const router = useRouter();
  const fresh = useRecentlyChanged(r.id);
  const tz = r.property.timezone;
  const at = kind === "departures" ? r.check_out : r.check_in;
  const open = () => router.push(`/reservations?r=${r.id}`);

  return (
    <li
      className={cn("group flex cursor-pointer items-center gap-3 px-4 py-2.5 transition-colors hover:bg-muted/45", fresh && "animate-row-in")}
      onClick={open}
      onKeyDown={(e) => {
        if (e.key === "Enter") open();
      }}
      tabIndex={0}
      data-testid={kind === "arrivals" ? "check-in-row" : kind === "departures" ? "check-out-row" : "in-house-row"}
    >
      <div className="w-12 shrink-0 text-right" title={`${formatInZone(at, tz)} · ${tz}`}>
        {kind === "in_house" ? (
          <>
            <div className="text-[11px] text-subtle-foreground">until</div>
            <div className="text-xs font-medium tabular">{formatShortDate(r.check_out, tz)}</div>
          </>
        ) : (
          <div className="font-mono text-[13px] font-medium tabular">{formatTime(at, tz)}</div>
        )}
      </div>
      <Avatar name={guestName(r.guest)} size="sm" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] font-medium">{guestName(r.guest)}</div>
        <div className="truncate text-xs text-muted-foreground">
          {r.property.name} · Unit {r.unit.name} · {plural(r.nights, "night")}
          {r.adults + r.children > 0 ? ` · ${plural(r.adults + r.children, "guest")}` : ""}
        </div>
      </div>
      <StatusBadge status={r.status} className="hidden sm:inline-flex" />
      {canWrite ? (
        <div className="shrink-0" onClick={(e) => e.stopPropagation()}>
          <StatusActions
            reservation={r}
            size="xs"
            includeCancel={false}
            only={kind === "arrivals" ? ["CHECKED_IN", "CONFIRMED"] : ["CHECKED_OUT"]}
          />
        </div>
      ) : null}
    </li>
  );
}

const EMPTY: Record<Kind, { title: string; description: string }> = {
  arrivals: { title: "No arrivals today", description: "New check-ins scheduled for today will appear here." },
  departures: { title: "No departures today", description: "Guests checking out today will appear here." },
  in_house: { title: "Nobody in house", description: "Checked-in guests appear here until they depart." },
};

export function TodaysOperations({ data, canWrite }: { data: CommandCenter; canWrite: boolean }) {
  const [tab, setTab] = useState<Kind>(data.check_ins.length || !data.check_outs.length ? "arrivals" : "departures");
  const rows = tab === "arrivals" ? data.check_ins : tab === "departures" ? data.check_outs : data.in_house;

  return (
    <Panel
      title="Today's operations"
      description="Arrivals, departures and active stays, in each property's local time"
      icon={<CalendarCheck2 />}
      bodyClassName="flex flex-col"
    >
      <Tabs
        label="Today's operations"
        className="px-2"
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "arrivals", label: "Check-ins", count: data.check_ins.length },
          { value: "departures", label: "Check-outs", count: data.check_outs.length },
          { value: "in_house", label: "Active stays", count: data.in_house.length },
        ]}
      />
      {rows.length === 0 ? (
        <EmptyState compact icon={CalendarCheck2} {...EMPTY[tab]} />
      ) : (
        <ul className="divide-y" aria-label={tab}>
          {rows.map((r) => (
            <OperationRow key={r.id} r={r} kind={tab} canWrite={canWrite} />
          ))}
        </ul>
      )}
    </Panel>
  );
}
