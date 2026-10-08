"use client";

import { type Reservation, utcToZoned, zonedToUtcIso } from "@stayops/shared";
import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/form";
import { Segmented } from "@/components/ui/segmented";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { can, useCurrentOrganization } from "@/features/organizations/api";
import { useProperties } from "@/features/properties/api";
import { useMoveReservation } from "@/features/reservations/api";
import { ReservationFormSheet, type ReservationPrefill } from "@/features/reservations/components/reservation-form";
import { ApiError } from "@/lib/api/client";
import { guestName } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useCalendar } from "./api";
import { CalendarGrid, type MoveRequest } from "./calendar-grid";
import { addDays, type CalendarMode, rangeDays, shiftAnchor, visibleRange } from "./layout";

function browserToday(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function rangeTitle(start: string, days: number, mode: CalendarMode): string {
  const a = new Date(`${start}T12:00:00Z`);
  if (mode === "month") return a.toLocaleDateString("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
  const b = new Date(`${addDays(start, days - 1)}T12:00:00Z`);
  const sameMonth = a.getUTCMonth() === b.getUTCMonth();
  const left = a.toLocaleDateString("en-GB", { day: "numeric", month: sameMonth ? undefined : "short", timeZone: "UTC" });
  const right = b.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  return `${left} – ${right}`;
}

const LEGEND: { status: Reservation["status"]; label: string; className: string }[] = [
  { status: "CONFIRMED", label: "Confirmed", className: "border-accent/30 bg-accent-soft" },
  { status: "PENDING", label: "Pending", className: "border-dashed border-warning/50 bg-[#fbf3e4]" },
  { status: "CHECKED_IN", label: "In house", className: "border-success/35 bg-[#e3f2e9]" },
  { status: "CHECKED_OUT", label: "Checked out", className: "border-border-strong/70 bg-muted" },
];

/** Operational calendar: month/week, property → unit rows, stay bars. */
export function CalendarView({ onOpenReservation, initialDate }: { onOpenReservation: (id: string) => void; initialDate?: string }) {
  const org = useCurrentOrganization();
  const canWrite = can(org.data, "reservations:write");
  const today = browserToday();
  const [mode, setMode] = useState<CalendarMode>("month");
  const [anchor, setAnchor] = useState(() => (initialDate && /^\d{4}-\d{2}-\d{2}$/.test(initialDate) ? initialDate : today));
  const [propertyId, setPropertyId] = useState("");
  const [showCancelled, setShowCancelled] = useState(false);
  const [prefill, setPrefill] = useState<ReservationPrefill | null>(null);
  const [saving, setSaving] = useState<Record<string, MoveRequest>>({});
  const move = useMoveReservation();
  const properties = useProperties();

  const { start, days } = visibleRange(anchor, mode);
  const dayList = rangeDays(start, days);
  const calendar = useCalendar({ start, end: addDays(start, days), property_id: propertyId || undefined, include_cancelled: showCancelled });

  const onMove = (req: MoveRequest) => {
    const r = req.reservation;
    const tz = r.property.timezone;
    // Keep the original local check-in/out times; only the dates (and unit) change.
    const ci = utcToZoned(r.check_in, tz);
    const co = utcToZoned(r.check_out, tz);
    setSaving((s) => ({ ...s, [r.id]: req }));
    move.mutate(
      {
        id: r.id,
        unit_id: req.unitId,
        check_in: zonedToUtcIso(req.checkInDate, ci.time, tz),
        check_out: zonedToUtcIso(req.checkOutDate, co.time, tz),
        expected_version: r.version,
      },
      {
        onSuccess: (updated) =>
          toast.success(`Moved ${updated.confirmation_code}`, {
            description: `${guestName(updated.guest)} · Unit ${updated.unit.name} · ${req.checkInDate} → ${req.checkOutDate}`,
          }),
        onError: (error) =>
          toast.error(error instanceof ApiError ? error.message : "The reservation could not be moved.", {
            description: error instanceof ApiError && error.isConflict ? "Nothing was changed." : undefined,
          }),
        onSettled: () =>
          setSaving((s) => {
            const next = { ...s };
            delete next[r.id];
            return next;
          }),
      },
    );
  };

  return (
    <div className="overflow-hidden rounded-lg border bg-surface shadow-xs" data-testid="calendar">
      <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
        <div className="flex items-center gap-1">
          <Button size="icon-sm" onClick={() => setAnchor(shiftAnchor(anchor, mode, -1))} aria-label={mode === "month" ? "Previous month" : "Previous week"}>
            <ChevronLeft />
          </Button>
          <Button size="sm" onClick={() => setAnchor(today)}>
            Today
          </Button>
          <Button size="icon-sm" onClick={() => setAnchor(shiftAnchor(anchor, mode, 1))} aria-label={mode === "month" ? "Next month" : "Next week"}>
            <ChevronRight />
          </Button>
        </div>
        <h2 className="min-w-40 px-1 text-[15px] font-semibold" aria-live="polite" data-testid="calendar-title">
          {rangeTitle(start, days, mode)}
        </h2>
        {calendar.isFetching && !calendar.isPending ? <span className="text-xs text-subtle-foreground">Updating…</span> : null}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Select wrapperClassName="w-44" value={propertyId} onChange={(e) => setPropertyId(e.target.value)} aria-label="Calendar property">
            <option value="">All properties</option>
            {properties.data?.items.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </Select>
          <label className="flex h-8 cursor-pointer items-center gap-2 rounded-md px-2 text-xs text-muted-foreground hover:bg-muted">
            <input type="checkbox" className="size-3.5 accent-[var(--accent)]" checked={showCancelled} onChange={(e) => setShowCancelled(e.target.checked)} />
            Show cancelled
          </label>
          <Segmented
            label="Calendar range"
            value={mode}
            onChange={(m) => setMode(m)}
            options={[
              { value: "month", label: "Month" },
              { value: "week", label: "Week" },
            ]}
          />
        </div>
      </div>

      {calendar.isPending ? (
        <div className="space-y-2 p-4" aria-busy="true" aria-label="Loading calendar">
          <Skeleton className="h-10 w-full" />
          {Array.from({ length: 7 }, (_, i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
      ) : calendar.error ? (
        <ErrorState error={calendar.error} onRetry={() => calendar.refetch()} />
      ) : calendar.data.properties.every((p) => p.units.length === 0) ? (
        <EmptyState icon={CalendarDays} title="No units to show" description="Add a property and its units to start planning stays." />
      ) : (
        <div className={cn(calendar.isPlaceholderData && "opacity-60 transition-opacity")}>
          <CalendarGrid
            data={calendar.data}
            dayList={dayList}
            mode={mode}
            today={today}
            canWrite={canWrite}
            saving={saving}
            onOpen={onOpenReservation}
            onCreate={(req) => setPrefill(req)}
            onMove={onMove}
            onBlockedMove={(r, unitName) =>
              toast.error(`Unit ${unitName} is unavailable for these dates.`, { description: `${guestName(r.guest)} · ${r.confirmation_code} was not moved.` })
            }
          />
        </div>
      )}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t px-4 py-2.5 text-[11px] text-muted-foreground">
        {LEGEND.map((l) => (
          <span key={l.status} className="inline-flex items-center gap-1.5">
            <span aria-hidden className={cn("h-2.5 w-4 rounded-[3px] border", l.className)} />
            {l.label}
          </span>
        ))}
        <span className="ml-auto hidden sm:inline">
          {canWrite ? "Drag across empty days to create · drag a confirmed or pending stay to move it" : "Read-only"} · dates in each property&apos;s timezone
        </span>
      </div>

      <ReservationFormSheet open={Boolean(prefill)} onOpenChange={(open) => !open && setPrefill(null)} prefill={prefill ?? undefined} />
    </div>
  );
}
