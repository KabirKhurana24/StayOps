"use client";

import { RESERVATION_SOURCES, RESERVATION_STATUSES, type ReservationSource, type ReservationStatus } from "@stayops/shared";
import { BedDouble, CalendarDays, Plus, Rows3 } from "lucide-react";
import { useDeferredValue } from "react";

import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/panel";
import { Segmented } from "@/components/ui/segmented";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { CalendarView } from "@/features/calendar/calendar-view";
import { can, useCurrentOrganization } from "@/features/organizations/api";
import { useReservations } from "@/features/reservations/api";
import { ReservationDrawer } from "@/features/reservations/components/reservation-drawer";
import { activeFilterCount, EMPTY_FILTERS, ReservationFilters, type ReservationFilterState } from "@/features/reservations/components/reservation-filters";
import { ReservationFormSheet } from "@/features/reservations/components/reservation-form";
import { ReservationTable } from "@/features/reservations/components/reservation-table";
import { plural } from "@/lib/format";
import { useSearchState } from "@/lib/use-search-state";

const PAGE_SIZE = 50;

function readFilters(get: (k: string) => string): ReservationFilterState {
  return {
    q: get("q"),
    statuses: get("status")
      .split(",")
      .filter((s): s is ReservationStatus => (RESERVATION_STATUSES as readonly string[]).includes(s)),
    source: (RESERVATION_SOURCES as readonly string[]).includes(get("source")) ? (get("source") as ReservationSource) : "",
    propertyId: get("property"),
    unitId: get("unit"),
    from: get("from"),
    to: get("to"),
  };
}

export function ReservationsPage() {
  const { get, set } = useSearchState();
  const org = useCurrentOrganization();
  const canWrite = can(org.data, "reservations:write");

  const view = get("view") === "calendar" ? "calendar" : "table";
  const filters = readFilters(get);
  const page = Math.max(0, Number(get("page")) || 0);
  const openId = get("r") || null;
  const creating = get("new") === "1";
  const q = useDeferredValue(filters.q.trim());

  const setFilters = (next: ReservationFilterState) =>
    set({
      q: next.q,
      status: next.statuses.join(","),
      source: next.source,
      property: next.propertyId,
      unit: next.unitId,
      from: next.from,
      to: next.to,
      page: null,
    });

  const reservations = useReservations(
    {
      q: q || undefined,
      status: filters.statuses.length ? filters.statuses : undefined,
      source: filters.source ? [filters.source] : undefined,
      property_id: filters.propertyId || undefined,
      unit_id: filters.unitId || undefined,
      // Stay overlaps the selected calendar days (UTC day bounds across properties).
      start: filters.from ? `${filters.from}T00:00:00Z` : undefined,
      end: filters.to ? `${filters.to}T23:59:59Z` : undefined,
      sort: "-check_in",
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    },
    { enabled: view === "table" },
  );

  const total = reservations.data?.total ?? 0;
  const filtered = activeFilterCount(filters) > 0;

  return (
    <div>
      <PageHeader
        title="Reservations"
        description="Manage stays, availability and guest bookings."
        actions={
          <>
            <Segmented
              label="View"
              value={view}
              onChange={(v) => set({ view: v === "table" ? null : v, page: null })}
              options={[
                { value: "table", label: "Table", icon: <Rows3 /> },
                { value: "calendar", label: "Calendar", icon: <CalendarDays /> },
              ]}
            />
            {canWrite ? (
              <Button variant="primary" onClick={() => set({ new: "1" }, { push: true })}>
                <Plus /> New reservation
              </Button>
            ) : null}
          </>
        }
      />

      {view === "calendar" ? (
        <CalendarView onOpenReservation={(id) => set({ r: id }, { push: true })} />
      ) : (
        <div className="overflow-hidden rounded-lg border bg-surface shadow-xs">
          <ReservationFilters value={filters} onChange={setFilters} />
          {reservations.isPending ? (
            <SkeletonRows rows={10} columns={7} />
          ) : reservations.error ? (
            <ErrorState error={reservations.error} onRetry={() => reservations.refetch()} />
          ) : reservations.data.items.length === 0 ? (
            <EmptyState
              icon={BedDouble}
              title={filtered ? "No reservations match these filters" : "No reservations yet"}
              description={filtered ? "Try widening the dates or clearing a filter." : "Create the first reservation to start filling your calendar."}
              action={
                filtered ? (
                  <Button size="sm" onClick={() => setFilters(EMPTY_FILTERS)}>
                    Clear filters
                  </Button>
                ) : canWrite ? (
                  <Button size="sm" variant="primary" onClick={() => set({ new: "1" }, { push: true })}>
                    <Plus /> New reservation
                  </Button>
                ) : null
              }
            />
          ) : (
            <div className={reservations.isPlaceholderData ? "opacity-60 transition-opacity" : undefined}>
              <ReservationTable rows={reservations.data.items} canWrite={canWrite} onOpen={(id) => set({ r: id }, { push: true })} />
              <div className="flex items-center justify-between border-t px-4 py-2.5 text-xs text-muted-foreground">
                <span className="tabular">
                  {total > PAGE_SIZE
                    ? `${page * PAGE_SIZE + 1}–${Math.min((page + 1) * PAGE_SIZE, total)} of ${total}`
                    : plural(total, "reservation")}
                </span>
                {total > PAGE_SIZE ? (
                  <div className="flex gap-1.5">
                    <Button size="sm" disabled={page === 0} onClick={() => set({ page: page > 1 ? String(page - 1) : null })}>
                      Previous
                    </Button>
                    <Button size="sm" disabled={(page + 1) * PAGE_SIZE >= total} onClick={() => set({ page: String(page + 1) })}>
                      Next
                    </Button>
                  </div>
                ) : null}
              </div>
            </div>
          )}
        </div>
      )}

      <ReservationDrawer id={openId} onClose={() => set({ r: null })} />
      <ReservationFormSheet open={creating} onOpenChange={(open) => set({ new: open ? "1" : null })} />
    </div>
  );
}
