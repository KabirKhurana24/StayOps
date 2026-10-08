"use client";

import { type CalendarData, type CalendarProperty, type CalendarUnit, type Reservation, utcToZoned } from "@stayops/shared";
import { Wrench } from "lucide-react";
import { type PointerEvent as ReactPointerEvent, useRef, useState } from "react";

import { useRecentlyChanged } from "@/features/realtime/realtime-provider";
import { guestName, plural, STATUS_LABEL } from "@/lib/format";
import { cn } from "@/lib/utils";

import { addDays, assignLanes, barGeometry, type CalendarMode, staysOverlap, weekdayIndex } from "./layout";

const LABEL_W = 184;
const LANE_H = 28;
const BAR_H = 22;
const ROW_PAD = 5;
const MIN_COL = { month: 30, week: 116 } as const;
const BLOCKING = new Set<Reservation["status"]>(["PENDING", "CONFIRMED", "CHECKED_IN"]);
const MOVABLE = new Set<Reservation["status"]>(["PENDING", "CONFIRMED"]);

const BAR_STYLE: Record<Reservation["status"], string> = {
  PENDING: "border-dashed border-warning/50 bg-[#fbf3e4] text-[#7d4d06]",
  CONFIRMED: "border-accent/30 bg-accent-soft text-[#0b5550]",
  CHECKED_IN: "border-success/35 bg-[#e3f2e9] text-[#155c38]",
  CHECKED_OUT: "border-border-strong/70 bg-muted text-muted-foreground",
  CANCELLED: "border-dashed border-danger/30 bg-surface text-danger/70 line-through",
};

const SOURCE_SWATCH: Record<Reservation["source"], string> = {
  AIRBNB: "bg-[#e5484d]",
  BOOKING_COM: "bg-[#1f5fbf]",
  VRBO: "bg-[#2b6fd6]",
  EXPEDIA: "bg-[#d9a400]",
  DIRECT: "bg-accent",
  OTHER: "bg-subtle-foreground",
};

export type CreateRequest = { propertyId: string; unitId: string; checkIn: string; checkOut: string };
export type MoveRequest = { reservation: Reservation; unitId: string; checkInDate: string; checkOutDate: string };

type LocalStay = { r: Reservation; checkIn: string; checkOut: string; unitId: string };
type Drag = { id: string; originX: number; dayWidth: number; dx: number; unitId: string; moved: boolean; conflict: boolean };
type Selection = { unitId: string; propertyId: string; from: number; to: number };

function localStay(r: Reservation): LocalStay {
  const tz = r.property.timezone;
  return { r, checkIn: utcToZoned(r.check_in, tz).date, checkOut: utcToZoned(r.check_out, tz).date, unitId: r.unit.id };
}

function Bar({
  stay,
  geometry,
  lane,
  days,
  mode,
  preview,
  saving,
  draggable,
  onPointerDown,
  onOpen,
}: {
  stay: LocalStay;
  geometry: { start: number; end: number; clippedStart: boolean; clippedEnd: boolean };
  lane: number;
  days: number;
  mode: CalendarMode;
  preview?: { conflict: boolean } | null;
  saving: boolean;
  draggable: boolean;
  onPointerDown: (e: ReactPointerEvent<HTMLButtonElement>) => void;
  onOpen: () => void;
}) {
  const { r } = stay;
  const fresh = useRecentlyChanged(r.id);
  const name = guestName(r.guest);
  const widthDays = geometry.end - geometry.start;
  return (
    <button
      type="button"
      data-testid="calendar-bar"
      data-reservation-id={r.id}
      data-check-in={stay.checkIn}
      data-check-out={stay.checkOut}
      data-unit-id={stay.unitId}
      data-status={r.status}
      title={`${name} · ${r.confirmation_code} · ${stay.checkIn} → ${stay.checkOut} · ${STATUS_LABEL[r.status]}${draggable ? " · drag to move" : ""}`}
      aria-label={`${name}, ${r.confirmation_code}, ${stay.checkIn} to ${stay.checkOut}, ${STATUS_LABEL[r.status]}`}
      onPointerDown={onPointerDown}
      onClick={(e) => {
        // Pointer handlers open on click-without-drag; keyboard activation lands here.
        if (e.detail === 0) onOpen();
      }}
      className={cn(
        "absolute z-[2] flex items-center gap-1.5 overflow-hidden rounded-[5px] border px-1.5 text-left text-[11px] leading-none font-medium whitespace-nowrap shadow-xs transition-[box-shadow,opacity,transform] duration-150 select-none hover:z-[3] hover:shadow-sm focus-visible:z-[3]",
        BAR_STYLE[r.status],
        geometry.clippedStart && "rounded-l-none border-l-0",
        geometry.clippedEnd && "rounded-r-none border-r-0",
        draggable ? "cursor-grab active:cursor-grabbing" : "cursor-pointer",
        preview && "z-[4] shadow-popover ring-2",
        preview && (preview.conflict ? "ring-danger/60" : "ring-accent/50"),
        saving && "animate-pulse opacity-70",
        fresh && !preview && "ring-2 ring-accent/30",
      )}
      style={{
        left: `calc(${(geometry.start / days) * 100}% + 1px)`,
        width: `calc(${(widthDays / days) * 100}% - 2px)`,
        top: ROW_PAD + lane * LANE_H,
        height: BAR_H,
      }}
    >
      <span aria-hidden className={cn("size-1.5 shrink-0 rounded-[2px]", SOURCE_SWATCH[r.source])} />
      <span className="truncate">{name}</span>
      {mode === "week" || widthDays >= 4 ? <span className="truncate font-normal opacity-70">{plural(r.nights, "night")}</span> : null}
      {saving ? <span className="ml-auto shrink-0 font-normal opacity-80">Saving…</span> : null}
    </button>
  );
}

function UnitRow({
  property,
  unit,
  stays,
  dayList,
  mode,
  today,
  canWrite,
  drag,
  saving,
  selection,
  onBarPointerDown,
  onTrackPointerDown,
  onOpen,
}: {
  property: CalendarProperty;
  unit: CalendarUnit;
  stays: LocalStay[];
  dayList: string[];
  mode: CalendarMode;
  today: string;
  canWrite: boolean;
  drag: Drag | null;
  saving: Record<string, MoveRequest>;
  selection: Selection | null;
  onBarPointerDown: (e: ReactPointerEvent<HTMLButtonElement>, stay: LocalStay) => void;
  onTrackPointerDown: (e: ReactPointerEvent<HTMLDivElement>, unit: CalendarUnit) => void;
  onOpen: (id: string) => void;
}) {
  const days = dayList.length;
  const start = dayList[0] ?? today;
  const bookable = unit.status === "ACTIVE" && property.status === "ACTIVE";

  // Place each stay at its current, previewed (dragging) or saving position.
  const placed = stays.flatMap((stay) => {
    const pendingMove = saving[stay.r.id];
    let s = stay;
    if (pendingMove) s = { ...stay, checkIn: pendingMove.checkInDate, checkOut: pendingMove.checkOutDate, unitId: pendingMove.unitId };
    else if (drag?.id === stay.r.id && drag.moved) s = { ...stay, checkIn: addDays(stay.checkIn, drag.dx), checkOut: addDays(stay.checkOut, drag.dx), unitId: drag.unitId };
    if (s.unitId !== unit.id) return [];
    const g = barGeometry(s.checkIn, s.checkOut, start, days);
    return g ? [{ ...g, stay: s }] : [];
  });
  // Bars dragged *into* this row from another unit of the same property.
  const { lanes, placed: laned } = assignLanes(placed);
  const height = ROW_PAD * 2 + lanes * LANE_H - (LANE_H - BAR_H);

  return (
    <div className="flex border-b last:border-b-0" data-unit-row={unit.id} data-property-id={property.id} data-testid="calendar-unit-row">
      <div className="sticky left-0 z-[5] flex shrink-0 items-center gap-2 border-r bg-surface px-3" style={{ width: LABEL_W }}>
        <span className="truncate text-[13px] font-medium">{unit.name}</span>
        <span className="truncate text-[11px] text-subtle-foreground lowercase">{unit.type}</span>
        {unit.status !== "ACTIVE" ? (
          <span className="ml-auto inline-flex items-center gap-1 text-[10px] font-medium text-warning uppercase" title={`Unit is ${unit.status.toLowerCase()}`}>
            <Wrench className="size-3" /> {unit.status === "MAINTENANCE" ? "maint." : "inactive"}
          </span>
        ) : null}
      </div>
      <div
        className={cn("relative min-w-0 flex-1", bookable && canWrite ? "cursor-cell" : "cursor-default")}
        style={{ height }}
        onPointerDown={(e) => bookable && canWrite && onTrackPointerDown(e, unit)}
        data-track
      >
        <div className="absolute inset-0 grid" style={{ gridTemplateColumns: `repeat(${days}, minmax(0, 1fr))` }} aria-hidden>
          {dayList.map((d) => (
            <div
              key={d}
              className={cn(
                "border-r border-border/70 last:border-r-0",
                weekdayIndex(d) >= 5 && "bg-muted/35",
                d === today && "bg-accent-soft/60",
                !bookable && "bg-[repeating-linear-gradient(135deg,transparent,transparent_5px,rgb(0_0_0/0.025)_5px,rgb(0_0_0/0.025)_10px)]",
              )}
            />
          ))}
        </div>
        {selection && selection.unitId === unit.id ? (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-y-1 z-[1] rounded-[5px] border border-dashed border-accent bg-accent/10"
            style={{
              left: `${(Math.min(selection.from, selection.to) / days) * 100}%`,
              width: `${((Math.abs(selection.to - selection.from) + 1) / days) * 100}%`,
            }}
          />
        ) : null}
        {laned.map(({ stay, lane, ...g }) => (
          <Bar
            key={stay.r.id}
            stay={stay}
            geometry={g}
            lane={lane}
            days={days}
            mode={mode}
            preview={drag?.id === stay.r.id && drag.moved ? { conflict: drag.conflict } : null}
            saving={Boolean(saving[stay.r.id])}
            draggable={canWrite && MOVABLE.has(stay.r.status) && !saving[stay.r.id]}
            onPointerDown={(e) => onBarPointerDown(e, stay)}
            onOpen={() => onOpen(stay.r.id)}
          />
        ))}
      </div>
    </div>
  );
}

/** Property → unit rows × date columns, with stay bars. */
export function CalendarGrid({
  data,
  dayList,
  mode,
  today,
  canWrite,
  saving,
  onOpen,
  onCreate,
  onMove,
  onBlockedMove,
}: {
  data: CalendarData;
  dayList: string[];
  mode: CalendarMode;
  today: string;
  canWrite: boolean;
  saving: Record<string, MoveRequest>;
  onOpen: (id: string) => void;
  onCreate: (req: CreateRequest) => void;
  onMove: (req: MoveRequest) => void;
  onBlockedMove: (stay: Reservation, unitName: string) => void;
}) {
  const days = dayList.length;
  const stays = data.reservations.map(localStay);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const dragRef = useRef<Drag | null>(null);
  const selectionRef = useRef<Selection | null>(null);

  const unitById = new Map(data.properties.flatMap((p) => p.units.map((u) => [u.id, { unit: u, property: p }] as const)));

  const conflicts = (stay: LocalStay, unitId: string, dx: number) => {
    const moved = { checkIn: addDays(stay.checkIn, dx), checkOut: addDays(stay.checkOut, dx) };
    return stays.some((o) => o.r.id !== stay.r.id && o.unitId === unitId && BLOCKING.has(o.r.status) && staysOverlap(moved, o));
  };

  const onBarPointerDown = (e: ReactPointerEvent<HTMLButtonElement>, stay: LocalStay) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    const track = e.currentTarget.parentElement;
    const dayWidth = track ? track.getBoundingClientRect().width / days : 40;
    const movable = canWrite && MOVABLE.has(stay.r.status) && !saving[stay.r.id];
    const state: Drag = { id: stay.r.id, originX: e.clientX, dayWidth, dx: 0, unitId: stay.unitId, moved: false, conflict: false };
    dragRef.current = state;
    const target = e.currentTarget;
    target.setPointerCapture(e.pointerId);

    const move = (ev: PointerEvent) => {
      const cur = dragRef.current;
      if (!cur || !movable) return;
      const dx = Math.round((ev.clientX - cur.originX) / cur.dayWidth);
      const moved = cur.moved || Math.abs(ev.clientX - cur.originX) > 4;
      // Vertical moves are limited to units of the same property (same timezone).
      const row = document.elementFromPoint(ev.clientX, ev.clientY)?.closest<HTMLElement>("[data-unit-row]");
      const candidate = row?.dataset.unitRow;
      const sameProperty = candidate && row?.dataset.propertyId === stay.r.property.id && unitById.get(candidate)?.unit.status === "ACTIVE";
      const unitId = sameProperty ? candidate : cur.unitId;
      const next = { ...cur, dx, moved, unitId, conflict: conflicts(stay, unitId, dx) };
      dragRef.current = next;
      setDrag(next);
    };
    const up = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", up);
      target.removeEventListener("pointercancel", up);
      const cur = dragRef.current;
      dragRef.current = null;
      setDrag(null);
      if (!cur || !cur.moved) {
        onOpen(stay.r.id);
        return;
      }
      if (cur.dx === 0 && cur.unitId === stay.unitId) return;
      if (cur.conflict) {
        onBlockedMove(stay.r, unitById.get(cur.unitId)?.unit.name ?? stay.r.unit.name);
        return;
      }
      onMove({ reservation: stay.r, unitId: cur.unitId, checkInDate: addDays(stay.checkIn, cur.dx), checkOutDate: addDays(stay.checkOut, cur.dx) });
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", up);
    target.addEventListener("pointercancel", up);
  };

  const onTrackPointerDown = (e: ReactPointerEvent<HTMLDivElement>, unit: CalendarUnit) => {
    if (e.button !== 0) return;
    const track = e.currentTarget;
    const rect = track.getBoundingClientRect();
    const indexAt = (x: number) => Math.min(days - 1, Math.max(0, Math.floor(((x - rect.left) / rect.width) * days)));
    const propertyId = unitById.get(unit.id)?.property.id ?? "";
    const first = indexAt(e.clientX);
    const sel: Selection = { unitId: unit.id, propertyId, from: first, to: first };
    selectionRef.current = sel;
    setSelection(sel);
    track.setPointerCapture(e.pointerId);

    const move = (ev: PointerEvent) => {
      const cur = selectionRef.current;
      if (!cur) return;
      const next = { ...cur, to: indexAt(ev.clientX) };
      selectionRef.current = next;
      setSelection(next);
    };
    const up = () => {
      track.removeEventListener("pointermove", move);
      track.removeEventListener("pointerup", up);
      track.removeEventListener("pointercancel", up);
      const cur = selectionRef.current;
      selectionRef.current = null;
      setSelection(null);
      if (!cur) return;
      const lo = Math.min(cur.from, cur.to);
      const hi = Math.max(cur.from, cur.to);
      const checkIn = dayList[lo];
      const lastNight = dayList[hi];
      if (checkIn && lastNight) onCreate({ propertyId: cur.propertyId, unitId: cur.unitId, checkIn, checkOut: addDays(lastNight, 1) });
    };
    track.addEventListener("pointermove", move);
    track.addEventListener("pointerup", up);
    track.addEventListener("pointercancel", up);
  };

  const byUnit = new Map<string, LocalStay[]>();
  for (const s of stays) byUnit.set(s.unitId, [...(byUnit.get(s.unitId) ?? []), s]);
  // Stays being dragged/saved into another unit are rendered by the target row too.
  const movingStays = stays.filter((s) => (drag?.id === s.r.id && drag.moved && drag.unitId !== s.unitId) || (saving[s.r.id] && saving[s.r.id]?.unitId !== s.unitId));

  const minWidth = LABEL_W + days * MIN_COL[mode];

  return (
    <div className="scrollbar-thin overflow-x-auto" data-testid="calendar-grid" data-mode={mode}>
      <div style={{ minWidth }} className="touch-pan-x">
        <div className="sticky top-0 z-[6] flex border-b bg-surface-raised">
          <div className="sticky left-0 z-[7] flex shrink-0 items-end border-r bg-surface-raised px-3 pb-1.5 text-[11px] font-medium text-muted-foreground" style={{ width: LABEL_W }}>
            Unit
          </div>
          <div className="grid flex-1" style={{ gridTemplateColumns: `repeat(${days}, minmax(0, 1fr))` }}>
            {dayList.map((d) => {
              const date = new Date(`${d}T12:00:00Z`);
              const isToday = d === today;
              return (
                <div
                  key={d}
                  data-date={d}
                  className={cn(
                    "flex flex-col items-center border-r border-border/70 py-1.5 last:border-r-0",
                    weekdayIndex(d) >= 5 && "bg-muted/40",
                    isToday && "bg-accent-soft",
                  )}
                >
                  <span className={cn("text-[10px] font-medium uppercase", isToday ? "text-accent" : "text-subtle-foreground")}>
                    {date.toLocaleDateString("en-GB", { weekday: mode === "week" ? "short" : "narrow", timeZone: "UTC" })}
                  </span>
                  <span
                    className={cn(
                      "mt-0.5 grid size-5 place-items-center rounded-full text-xs tabular",
                      isToday ? "bg-accent font-semibold text-white" : "text-foreground",
                    )}
                  >
                    {date.getUTCDate()}
                  </span>
                  {mode === "week" ? <span className="text-[10px] text-subtle-foreground">{date.toLocaleDateString("en-GB", { month: "short", timeZone: "UTC" })}</span> : null}
                </div>
              );
            })}
          </div>
        </div>

        {data.properties.map((p) =>
          p.units.length === 0 ? null : (
            <section key={p.id} aria-label={p.name}>
              <div className="flex border-b bg-surface-raised/70">
                <div className="sticky left-0 z-[5] flex items-center gap-2 bg-surface-raised/95 px-3 py-1.5" style={{ width: LABEL_W }}>
                  <span className="truncate text-xs font-semibold">{p.name}</span>
                  <span className="text-[11px] text-subtle-foreground tabular">{p.units.length}</span>
                </div>
                <div className="flex-1 truncate py-1.5 text-[11px] text-subtle-foreground">{p.timezone.replace("_", " ")}</div>
              </div>
              {p.units.map((u) => (
                <UnitRow
                  key={u.id}
                  property={p}
                  unit={u}
                  stays={[...(byUnit.get(u.id) ?? []), ...movingStays.filter((s) => s.r.property.id === p.id)]}
                  dayList={dayList}
                  mode={mode}
                  today={today}
                  canWrite={canWrite}
                  drag={drag}
                  saving={saving}
                  selection={selection}
                  onBarPointerDown={onBarPointerDown}
                  onTrackPointerDown={onTrackPointerDown}
                  onOpen={onOpen}
                />
              ))}
            </section>
          ),
        )}
      </div>
    </div>
  );
}
