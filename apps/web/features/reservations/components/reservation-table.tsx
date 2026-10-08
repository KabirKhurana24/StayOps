"use client";

import { type Reservation, utcToZoned } from "@stayops/shared";
import { ArrowUpRight, LogIn, LogOut, MoreHorizontal, Pencil } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { Avatar } from "@/components/ui/misc";
import { Table, TableScroll, Td, Th, THead, Tr } from "@/components/ui/table";
import { useRecentlyChanged } from "@/features/realtime/realtime-provider";
import { formatDay, formatMoney, formatStayRange, formatTime, guestName, plural } from "@/lib/format";
import { cn } from "@/lib/utils";

import { ReservationFormSheet } from "./reservation-form";
import { ACTION_LABEL, useStatusTransition } from "./status-actions";
import { SourceLabel, StatusBadge } from "./status-badge";

function RowActions({ r, canWrite }: { r: Reservation; canWrite: boolean }) {
  const { run, pending, dialog, targets } = useStatusTransition(r);
  const [editing, setEditing] = useState(false);
  const editable = canWrite && r.status !== "CHECKED_OUT" && r.status !== "CANCELLED";
  return (
    <div onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
      <Menu>
        <MenuTrigger asChild>
          <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${r.confirmation_code}`} className="opacity-70 group-hover:opacity-100 data-[state=open]:opacity-100">
            <MoreHorizontal />
          </Button>
        </MenuTrigger>
        <MenuContent className="w-48">
          <MenuItem asChild>
            <Link href={`/reservations/${r.id}`}>
              <ArrowUpRight /> Open full page
            </Link>
          </MenuItem>
          {editable ? (
            <MenuItem icon={<Pencil />} onSelect={() => setEditing(true)}>
              Edit
            </MenuItem>
          ) : null}
          {canWrite
            ? targets
                .filter((s) => s === "CHECKED_IN" || s === "CHECKED_OUT" || s === "CONFIRMED")
                .map((s) => (
                  <MenuItem key={s} disabled={pending} icon={s === "CHECKED_OUT" ? <LogOut /> : <LogIn />} onSelect={() => run(s)}>
                    {ACTION_LABEL[s]}
                  </MenuItem>
                ))
            : null}
          {canWrite && targets.includes("CANCELLED") ? (
            <>
              <MenuSeparator />
              <MenuItem tone="danger" disabled={pending} onSelect={() => run("CANCELLED")}>
                Cancel reservation
              </MenuItem>
            </>
          ) : null}
        </MenuContent>
      </Menu>
      {dialog}
      <ReservationFormSheet open={editing} onOpenChange={setEditing} reservation={r} />
    </div>
  );
}

function Row({ r, onOpen, canWrite }: { r: Reservation; onOpen: (id: string) => void; canWrite: boolean }) {
  const fresh = useRecentlyChanged(r.id);
  const tz = r.property.timezone;
  const cancelled = r.status === "CANCELLED";
  return (
    <Tr
      interactive
      tabIndex={0}
      className={cn("group", fresh && "animate-row-in", cancelled && "text-muted-foreground")}
      onClick={() => onOpen(r.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter") onOpen(r.id);
      }}
      data-testid="reservation-row"
      data-code={r.confirmation_code}
      data-status={r.status}
      data-check-in={utcToZoned(r.check_in, tz).date}
      data-check-out={utcToZoned(r.check_out, tz).date}
    >
      <Td>
        <div className="flex items-center gap-2.5">
          <Avatar name={guestName(r.guest)} size="sm" className={cancelled ? "opacity-60" : undefined} />
          <div className="min-w-0">
            <div className={cn("truncate font-medium", cancelled && "line-through decoration-subtle-foreground/50")}>{guestName(r.guest)}</div>
            <div className="font-mono text-[11px] text-subtle-foreground">{r.confirmation_code}</div>
          </div>
        </div>
      </Td>
      <Td className="max-w-[180px] truncate">{r.property.name}</Td>
      <Td className="font-medium">{r.unit.name}</Td>
      <Td className="whitespace-nowrap">
        <div>{formatStayRange(r.check_in, r.check_out, tz)}</div>
        <div className="text-xs text-muted-foreground">
          {plural(r.nights, "night")} · {plural(r.adults + r.children, "guest")}
        </div>
      </Td>
      <Td className="whitespace-nowrap tabular">
        {formatDay(r.check_in, tz)} <span className="text-muted-foreground">{formatTime(r.check_in, tz)}</span>
      </Td>
      <Td className="whitespace-nowrap tabular">
        {formatDay(r.check_out, tz)} <span className="text-muted-foreground">{formatTime(r.check_out, tz)}</span>
      </Td>
      <Td>
        <StatusBadge status={r.status} />
      </Td>
      <Td className="text-right font-medium whitespace-nowrap tabular">{formatMoney(r.total_amount, r.currency)}</Td>
      <Td className="whitespace-nowrap">
        <SourceLabel source={r.source} />
      </Td>
      <Td className="w-10 pr-2!">
        <RowActions r={r} canWrite={canWrite} />
      </Td>
    </Tr>
  );
}

function MobileCard({ r, onOpen }: { r: Reservation; onOpen: (id: string) => void }) {
  const fresh = useRecentlyChanged(r.id);
  const tz = r.property.timezone;
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(r.id)}
        className={cn("flex w-full gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/45", fresh && "animate-row-in")}
        data-testid="reservation-card"
      >
        <Avatar name={guestName(r.guest)} size="md" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-[13px] font-medium">{guestName(r.guest)}</span>
            <span className="shrink-0 text-[13px] font-medium tabular">{formatMoney(r.total_amount, r.currency, { compact: true })}</span>
          </div>
          <div className="truncate text-xs text-muted-foreground">
            {r.property.name} · {r.unit.name} · {formatStayRange(r.check_in, r.check_out, tz)}
          </div>
          <div className="mt-1.5 flex items-center gap-2">
            <StatusBadge status={r.status} />
            <span className="font-mono text-[11px] text-subtle-foreground">{r.confirmation_code}</span>
          </div>
        </div>
      </button>
    </li>
  );
}

/** Reservations list: dense table on desktop, cards on small screens. */
export function ReservationTable({ rows, onOpen, canWrite }: { rows: Reservation[]; onOpen: (id: string) => void; canWrite: boolean }) {
  return (
    <>
      <TableScroll className="hidden md:block">
        <Table className="min-w-[1040px]">
          <THead>
            <tr>
              <Th>Guest</Th>
              <Th>Property</Th>
              <Th>Unit</Th>
              <Th>Stay</Th>
              <Th>Check-in</Th>
              <Th>Check-out</Th>
              <Th>Status</Th>
              <Th className="text-right">Amount</Th>
              <Th>Source</Th>
              <Th>
                <span className="sr-only">Actions</span>
              </Th>
            </tr>
          </THead>
          <tbody>
            {rows.map((r) => (
              <Row key={r.id} r={r} onOpen={onOpen} canWrite={canWrite} />
            ))}
          </tbody>
        </Table>
      </TableScroll>
      <ul className="divide-y md:hidden">
        {rows.map((r) => (
          <MobileCard key={r.id} r={r} onOpen={onOpen} />
        ))}
      </ul>
    </>
  );
}
