"use client";

import { Building2 } from "lucide-react";
import Link from "next/link";

import { Meter } from "@/components/ui/misc";
import { Panel } from "@/components/ui/panel";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { useProperties } from "@/features/properties/api";
import { formatDay, formatTime } from "@/lib/format";

/** Per-property occupancy and today's movements (property stats read model). */
export function OperationalOverview() {
  const properties = useProperties();

  return (
    <Panel
      title="Operational overview"
      description="Occupancy right now and today's movements per property"
      icon={<Building2 />}
      actions={
        <Link href="/properties" className="text-xs text-accent hover:underline">
          All properties
        </Link>
      }
    >
      {properties.isPending ? (
        <SkeletonRows rows={3} columns={4} />
      ) : properties.error ? (
        <ErrorState compact error={properties.error} onRetry={() => properties.refetch()} />
      ) : properties.data.items.length === 0 ? (
        <EmptyState compact title="No properties yet" description="Add a property to see its operational state." />
      ) : (
        <ul className="divide-y">
          {properties.data.items.map((p) => {
            const s = p.stats;
            const rate = s && s.active_units ? s.occupied_units / s.active_units : 0;
            return (
              <li key={p.id}>
                <Link href={`/properties/${p.id}`} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-6 gap-y-2 px-4 py-3 transition-colors hover:bg-muted/45 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_auto_auto]">
                  <div className="min-w-0">
                    <div className="truncate text-[13px] font-medium">{p.name}</div>
                    <div className="truncate text-xs text-muted-foreground">
                      {s?.next_arrival
                        ? `Next: ${s.next_arrival.guest_name} · ${formatDay(s.next_arrival.check_in, p.timezone)} ${formatTime(s.next_arrival.check_in, p.timezone)}`
                        : "No upcoming arrivals"}
                    </div>
                  </div>
                  <div className="order-last col-span-2 flex items-center gap-2.5 md:order-none md:col-span-1">
                    <Meter value={rate} label={`${p.name} occupancy`} className="flex-1" />
                    <span className="w-16 text-right text-xs text-muted-foreground tabular">
                      {s ? `${s.occupied_units}/${s.active_units}` : "—"} units
                    </span>
                  </div>
                  <div className="hidden text-xs text-muted-foreground md:block">
                    <span className="font-medium text-foreground tabular">{s?.arrivals_today ?? 0}</span> in ·{" "}
                    <span className="font-medium text-foreground tabular">{s?.departures_today ?? 0}</span> out
                  </div>
                  <div className="text-right">
                    {s?.maintenance_units ? (
                      <span className="text-xs font-medium text-warning">{s.maintenance_units} in maintenance</span>
                    ) : (
                      <span className="text-xs text-muted-foreground">{Math.round(rate * 100)}%</span>
                    )}
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
