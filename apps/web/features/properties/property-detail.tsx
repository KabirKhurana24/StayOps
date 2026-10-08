"use client";

import type { Property, Reservation, Unit } from "@stayops/shared";
import { ArrowLeft, BedDouble, Building2, CalendarDays, Clock, History, Pencil, Plus, Wrench } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { useBreadcrumb } from "@/components/shell/shell-context";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Metric, MetricStrip } from "@/components/ui/misc";
import { DescriptionItem, DescriptionList, Panel } from "@/components/ui/panel";
import { Tabs } from "@/components/ui/segmented";
import { Sheet } from "@/components/ui/sheet";
import { EmptyState, ErrorState, Skeleton, SkeletonRows } from "@/components/ui/states";
import { Table, TableScroll, Td, Th, THead, Tr } from "@/components/ui/table";
import { useAudit, useCommandCenter } from "@/features/activity/api";
import { ActivityTimeline } from "@/features/activity/components/activity-timeline";
import { can, useCurrentOrganization } from "@/features/organizations/api";
import { useProperty, useUnits, useUpdateProperty } from "@/features/properties/api";
import { useReservations } from "@/features/reservations/api";
import { ReservationDrawer } from "@/features/reservations/components/reservation-drawer";
import { ReservationTable } from "@/features/reservations/components/reservation-table";
import { StatusBadge } from "@/features/reservations/components/status-badge";
import { useWorkOrders } from "@/features/maintenance/api";
import { MaintenanceStatusBadge, PriorityLabel, UnitOpsBadge } from "@/features/operations/components/badges";
import { ApiError } from "@/lib/api/client";
import { formatDay, formatShortDate, formatTime, guestName, plural } from "@/lib/format";
import { useSearchState } from "@/lib/use-search-state";
import { cn } from "@/lib/utils";

import { PropertyForm, UnitForm } from "./components/property-forms";
import { cityFromTimezone, localClock, PropertyStateBadge } from "./components/property-state";

type Tab = "overview" | "units" | "reservations" | "operations" | "maintenance" | "activity";

const UNIT_TONE = { ACTIVE: "success", MAINTENANCE: "warning", INACTIVE: "neutral" } as const;

/** Upcoming/current stays for this property (next 90 days), grouped per unit. */
function useUnitOccupancy(propertyId: string) {
  const now = new Date();
  return useReservations({
    property_id: propertyId,
    status: ["PENDING", "CONFIRMED", "CHECKED_IN"],
    start: now.toISOString().slice(0, 13) + ":00:00Z",
    end: new Date(now.getTime() + 90 * 86_400_000).toISOString().slice(0, 10) + "T00:00:00Z",
    sort: "check_in",
    limit: 200,
  });
}

function UnitsTable({ units, stays, now, onOpen }: { units: Unit[]; stays: Reservation[]; now: number; onOpen: (id: string) => void }) {
  return (
    <TableScroll>
      <Table className="min-w-[680px]">
        <THead>
          <tr>
            <Th>Unit</Th>
            <Th>Status</Th>
            <Th>Operations</Th>
            <Th>Current guest</Th>
            <Th>Next arrival</Th>
          </tr>
        </THead>
        <tbody>
          {units.map((u) => {
            const mine = stays.filter((r) => r.unit.id === u.id);
            // "Current" means physically in house (checked in), not merely due today.
            const current = mine.find((r) => r.status === "CHECKED_IN");
            const overdue = !current ? mine.find((r) => r.status !== "CHECKED_IN" && Date.parse(r.check_in) <= now) : undefined;
            const next = mine.find((r) => Date.parse(r.check_in) > now && r.id !== overdue?.id);
            return (
              <Tr key={u.id} data-testid="unit-row" data-unit-name={u.name} data-ops-status={u.operational_status ?? ""}>
                <Td>
                  <div className="font-medium">{u.name}</div>
                  <div className="text-xs text-muted-foreground capitalize">{u.type.toLowerCase()}</div>
                </Td>
                <Td>
                  <Badge tone={UNIT_TONE[u.status]} dot>
                    {u.status.charAt(0) + u.status.slice(1).toLowerCase()}
                  </Badge>
                </Td>
                <Td>{u.operational_status ? <UnitOpsBadge status={u.operational_status} /> : null}</Td>
                <Td>
                  {current ? (
                    <button type="button" className="text-left hover:underline" onClick={() => onOpen(current.id)}>
                      <div>{guestName(current.guest)}</div>
                      <div className="text-xs text-muted-foreground">until {formatShortDate(current.check_out, current.property.timezone)}</div>
                    </button>
                  ) : overdue ? (
                    <button type="button" className="text-left hover:underline" onClick={() => onOpen(overdue.id)}>
                      <div className="text-warning">Awaiting {guestName(overdue.guest)}</div>
                      <div className="text-xs text-muted-foreground">not checked in yet</div>
                    </button>
                  ) : (
                    <span className="text-subtle-foreground">Vacant</span>
                  )}
                </Td>
                <Td>
                  {next ? (
                    <button type="button" className="text-left hover:underline" onClick={() => onOpen(next.id)}>
                      <div>{guestName(next.guest)}</div>
                      <div className="text-xs text-muted-foreground">
                        {formatDay(next.check_in, next.property.timezone)} · {formatTime(next.check_in, next.property.timezone)}
                      </div>
                    </button>
                  ) : (
                    <span className="text-subtle-foreground">None in 90 days</span>
                  )}
                </Td>
              </Tr>
            );
          })}
        </tbody>
      </Table>
    </TableScroll>
  );
}

function OperationsTab({ propertyId, onOpen }: { propertyId: string; onOpen: (id: string) => void }) {
  const cc = useCommandCenter();
  if (cc.isPending) return <SkeletonRows rows={4} columns={3} />;
  if (cc.error) return <ErrorState compact error={cc.error} onRetry={() => cc.refetch()} />;
  const lists = [
    { title: "Check-ins today", rows: cc.data.check_ins.filter((r) => r.property.id === propertyId), at: "check_in" as const },
    { title: "Check-outs today", rows: cc.data.check_outs.filter((r) => r.property.id === propertyId), at: "check_out" as const },
    { title: "In house", rows: cc.data.in_house.filter((r) => r.property.id === propertyId), at: "check_out" as const },
  ];
  const signals = cc.data.signals.filter((s) => s.property_id === propertyId);
  return (
    <div className="grid gap-5 lg:grid-cols-3">
      {lists.map((l) => (
        <Panel key={l.title} title={l.title} count={l.rows.length}>
          {l.rows.length === 0 ? (
            <EmptyState compact title="Nothing scheduled" />
          ) : (
            <ul className="divide-y">
              {l.rows.map((r) => (
                <li key={r.id}>
                  <button type="button" onClick={() => onOpen(r.id)} className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-muted/45">
                    <span className="w-11 font-mono text-xs tabular">{formatTime(r[l.at], r.property.timezone)}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] font-medium">{guestName(r.guest)}</span>
                      <span className="block text-xs text-muted-foreground">Unit {r.unit.name}</span>
                    </span>
                    <StatusBadge status={r.status} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      ))}
      {signals.length ? (
        <Panel title="Signals for this property" count={signals.length} className="lg:col-span-3">
          <ul className="divide-y">
            {signals.map((s, i) => (
              <li key={`${s.code}-${i}`} className="px-4 py-2.5">
                <div className="text-[13px] font-medium">{s.title}</div>
                <div className="text-xs text-muted-foreground">{s.detail}</div>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}
    </div>
  );
}

function Overview({ p, units, stays, now, onOpen }: { p: Property; units: Unit[]; stays: Reservation[]; now: number; onOpen: (id: string) => void }) {
  const s = p.stats;
  const upcoming = stays.filter((r) => Date.parse(r.check_in) > now).slice(0, 6);
  return (
    <div className="space-y-5">
      <MetricStrip label="Property metrics" columns={5}>
        <Metric icon={<BedDouble />} label="Units" value={p.unit_count} sub={s?.maintenance_units ? `${s.maintenance_units} in maintenance` : `${s?.active_units ?? units.length} active`} />
        <Metric label="Occupied now" value={`${s?.occupied_units ?? 0}/${s?.active_units ?? 0}`} sub={s?.active_units ? `${Math.round(((s.occupied_units ?? 0) / s.active_units) * 100)}% occupancy` : "No active units"} />
        <Metric label="Arrivals today" value={s?.arrivals_today ?? 0} />
        <Metric label="Departures today" value={s?.departures_today ?? 0} />
        <Metric
          label="Next arrival"
          value={s?.next_arrival ? formatShortDate(s.next_arrival.check_in, p.timezone) : "—"}
          sub={s?.next_arrival ? `${s.next_arrival.guest_name} · Unit ${s.next_arrival.unit_name}` : "None scheduled"}
        />
      </MetricStrip>
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        <Panel title="Upcoming stays" description="Next arrivals across all units">
          {upcoming.length === 0 ? (
            <EmptyState compact icon={CalendarDays} title="No upcoming stays" description="Nothing booked in the next 90 days." />
          ) : (
            <ul className="divide-y">
              {upcoming.map((r) => (
                <li key={r.id}>
                  <button type="button" onClick={() => onOpen(r.id)} className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-muted/45">
                    <span className="w-24 shrink-0 text-xs text-muted-foreground tabular">{formatDay(r.check_in, p.timezone)}</span>
                    <span className="min-w-0 flex-1 truncate text-[13px] font-medium">{guestName(r.guest)}</span>
                    <span className="text-xs text-muted-foreground">
                      Unit {r.unit.name} · {plural(r.nights, "night")}
                    </span>
                    <StatusBadge status={r.status} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="Details">
          <DescriptionList>
            <DescriptionItem label="Address">{p.address}</DescriptionItem>
            <DescriptionItem label="Timezone">
              {p.timezone} <span className="text-muted-foreground">· {localClock(p.timezone)} now</span>
            </DescriptionItem>
            <DescriptionItem label="Check-in">{p.default_check_in_time.slice(0, 5)}</DescriptionItem>
            <DescriptionItem label="Check-out">{p.default_check_out_time.slice(0, 5)}</DescriptionItem>
            <DescriptionItem label="Status">
              <PropertyStateBadge p={p} />
            </DescriptionItem>
          </DescriptionList>
        </Panel>
      </div>
    </div>
  );
}

function PropertyActivity({ propertyId }: { propertyId: string }) {
  const audit = useAudit({ entity_type: "property", entity_id: propertyId, limit: 50 });
  if (audit.isPending) return <SkeletonRows rows={4} columns={2} />;
  if (audit.error) return <ErrorState compact error={audit.error} onRetry={() => audit.refetch()} />;
  if (!audit.data.items.length) return <EmptyState compact icon={History} title="No activity yet" />;
  return <ActivityTimeline entries={audit.data.items} />;
}

function PropertyWorkOrders({ propertyId }: { propertyId: string }) {
  const orders = useWorkOrders({ property_id: propertyId, view: "active", limit: 50 });
  return (
    <Panel
      title="Active work orders"
      count={orders.data?.total}
      icon={<Wrench />}
      actions={
        <Link href={`/maintenance?property=${propertyId}`} className="text-xs text-accent hover:underline">
          Open maintenance
        </Link>
      }
    >
      {orders.isPending ? (
        <SkeletonRows rows={3} columns={3} />
      ) : orders.error ? (
        <ErrorState compact error={orders.error} onRetry={() => orders.refetch()} />
      ) : orders.data.items.length === 0 ? (
        <EmptyState compact icon={Wrench} title="No open work orders" />
      ) : (
        <ul className="divide-y">
          {orders.data.items.map((wo) => (
            <li key={wo.id}>
              <Link href={`/maintenance?wo=${wo.id}`} className="flex items-center gap-3 px-4 py-2.5 hover:bg-muted/40">
                <PriorityLabel priority={wo.priority} compact />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] font-medium">{wo.title}</div>
                  <div className="text-xs text-muted-foreground">
                    Unit {wo.unit.name}
                    {wo.unit_blocked ? " · blocks bookings" : ""}
                  </div>
                </div>
                <MaintenanceStatusBadge status={wo.status} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export function PropertyDetail({ id }: { id: string }) {
  const property = useProperty(id);
  const units = useUnits(id);
  const stays = useUnitOccupancy(id);
  const org = useCurrentOrganization();
  const update = useUpdateProperty(id);
  const { get, set } = useSearchState();
  const [editing, setEditing] = useState(false);
  const [addingUnit, setAddingUnit] = useState(false);
  const tab = (get("tab") || "overview") as Tab;
  const openId = get("r") || null;
  const openReservation = (rid: string) => set({ r: rid }, { push: true });
  useBreadcrumb(property.data?.name);

  const propertyReservations = useReservations({ property_id: id, sort: "-check_in", limit: 50 }, { enabled: tab === "reservations" });

  if (property.isPending) {
    return (
      <div className="space-y-4" aria-busy="true" aria-label="Loading property">
        <Skeleton className="h-14 w-80" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (property.error) {
    if (property.error instanceof ApiError && property.error.status === 404)
      return <EmptyState icon={Building2} title="Property not found" description="It may belong to another organization." />;
    return <ErrorState error={property.error} onRetry={() => property.refetch()} />;
  }
  const p = property.data;
  const unitList = units.data ?? [];
  const stayList = stays.data?.items ?? [];
  // Units that can't take bookings: manual maintenance status or an active blocking work order.
  const maintenance = unitList.filter((u) => u.status === "MAINTENANCE" || u.maintenance_blocked);

  return (
    <div>
      <Link href="/properties" className="mb-4 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3.5" /> Properties
      </Link>
      <div className="mb-5 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <span className="grid size-11 shrink-0 place-items-center rounded-lg border bg-surface text-muted-foreground shadow-xs" aria-hidden>
            <Building2 className="size-5" />
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-semibold tracking-[-0.01em]">{p.name}</h1>
              <PropertyStateBadge p={p} />
            </div>
            <p className="mt-0.5 truncate text-[13px] text-muted-foreground">{p.address}</p>
            <p className="mt-0.5 flex items-center gap-1 text-xs text-subtle-foreground">
              <Clock className="size-3" aria-hidden /> {cityFromTimezone(p.timezone)} · {localClock(p.timezone)} local · check-in {p.default_check_in_time.slice(0, 5)} / check-out{" "}
              {p.default_check_out_time.slice(0, 5)}
            </p>
          </div>
        </div>
        <div className="flex gap-2">
          <Link href="/calendar" className={buttonVariants({ variant: "secondary" })}>
            <CalendarDays /> Calendar
          </Link>
          {can(org.data, "properties:write") ? (
            <Button onClick={() => setEditing(true)}>
              <Pencil /> Edit
            </Button>
          ) : null}
        </div>
      </div>

      <Tabs
        label="Property sections"
        className="mb-5"
        value={tab}
        onChange={(t) => set({ tab: t === "overview" ? null : t })}
        tabs={[
          { value: "overview", label: "Overview" },
          { value: "units", label: "Units", count: unitList.length },
          { value: "reservations", label: "Reservations" },
          { value: "operations", label: "Operations" },
          { value: "maintenance", label: "Maintenance", count: maintenance.length || undefined },
          { value: "activity", label: "Activity" },
        ]}
      />

      <div className="animate-fade-up" key={tab}>
        {tab === "overview" ? (
          <Overview p={p} units={unitList} stays={stayList} now={stays.dataUpdatedAt} onOpen={openReservation} />
        ) : tab === "units" ? (
          <Panel
            title="Units"
            count={unitList.length}
            description="Live occupancy per unit"
            actions={
              can(org.data, "units:write") ? (
                <Button size="sm" onClick={() => setAddingUnit(true)}>
                  <Plus /> Add unit
                </Button>
              ) : null
            }
          >
            {units.isPending || stays.isPending ? (
              <SkeletonRows rows={4} columns={4} />
            ) : units.error ? (
              <ErrorState compact error={units.error} onRetry={() => units.refetch()} />
            ) : unitList.length === 0 ? (
              <EmptyState compact icon={BedDouble} title="No units yet" description="Units are what guests book." />
            ) : (
              <UnitsTable units={unitList} stays={stayList} now={stays.dataUpdatedAt} onOpen={openReservation} />
            )}
          </Panel>
        ) : tab === "reservations" ? (
          <div className="overflow-hidden rounded-lg border bg-surface shadow-xs">
            {propertyReservations.isPending ? (
              <SkeletonRows rows={6} columns={6} />
            ) : propertyReservations.error ? (
              <ErrorState error={propertyReservations.error} onRetry={() => propertyReservations.refetch()} />
            ) : propertyReservations.data.items.length === 0 ? (
              <EmptyState icon={BedDouble} title="No reservations for this property" />
            ) : (
              <ReservationTable rows={propertyReservations.data.items} canWrite={can(org.data, "reservations:write")} onOpen={openReservation} />
            )}
          </div>
        ) : tab === "operations" ? (
          <OperationsTab propertyId={p.id} onOpen={openReservation} />
        ) : tab === "maintenance" ? (
          <div className="space-y-5">
            <Panel title="Units in maintenance" count={maintenance.length} icon={<Wrench />}>
              {maintenance.length === 0 ? (
                <EmptyState compact title="No blocked units" description="Units in maintenance, or with a blocking work order, can't take new bookings." />
              ) : (
                <ul className="divide-y">
                  {maintenance.map((u) => {
                    const affected = stayList.filter((r) => r.unit.id === u.id);
                    return (
                      <li key={u.id} className="flex items-center gap-3 px-4 py-3">
                        <Wrench className="size-4 text-warning" aria-hidden />
                        <div className="min-w-0 flex-1">
                          <div className="text-[13px] font-medium">Unit {u.name}</div>
                          <div className={cn("text-xs", affected.length ? "text-danger" : "text-muted-foreground")}>
                            {affected.length ? `${plural(affected.length, "upcoming stay")} need reassignment` : "No upcoming stays affected"}
                          </div>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </Panel>
            <PropertyWorkOrders propertyId={p.id} />
          </div>
        ) : (
          <div className="overflow-hidden rounded-lg border bg-surface shadow-xs">
            <PropertyActivity propertyId={p.id} />
          </div>
        )}
      </div>

      <Sheet open={editing} onOpenChange={setEditing} title={`Edit ${p.name}`}>
        <PropertyForm
          initial={{ ...p, default_check_in_time: p.default_check_in_time.slice(0, 5), default_check_out_time: p.default_check_out_time.slice(0, 5) }}
          submitLabel="Save changes"
          pending={update.isPending}
          onCancel={() => setEditing(false)}
          error={update.error instanceof ApiError ? update.error.message : null}
          onSubmit={(values) => update.mutate(values, { onSuccess: () => setEditing(false) })}
        />
      </Sheet>
      <Sheet open={addingUnit} onOpenChange={setAddingUnit} title={`Add unit to ${p.name}`}>
        <UnitForm propertyId={p.id} onDone={() => setAddingUnit(false)} />
      </Sheet>
      <ReservationDrawer id={openId} onClose={() => set({ r: null })} />
    </div>
  );
}
