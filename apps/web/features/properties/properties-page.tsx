"use client";

import type { Property } from "@stayops/shared";
import { Building2, Clock, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Meter } from "@/components/ui/misc";
import { PageHeader } from "@/components/ui/panel";
import { Sheet } from "@/components/ui/sheet";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { Table, TableScroll, Td, Th, THead, Tr } from "@/components/ui/table";
import { can, useCurrentOrganization } from "@/features/organizations/api";
import { useCreateProperty, useProperties } from "@/features/properties/api";
import { ApiError } from "@/lib/api/client";
import { formatDay, formatTime, plural } from "@/lib/format";

import { PropertyForm } from "./components/property-forms";
import { cityFromTimezone, localClock, PropertyStateBadge } from "./components/property-state";

function NextArrival({ p }: { p: Property }) {
  const next = p.stats?.next_arrival;
  if (!next) return <span className="text-subtle-foreground">None scheduled</span>;
  return (
    <div className="min-w-0">
      <div className="truncate">{next.guest_name}</div>
      <div className="text-xs text-muted-foreground">
        {formatDay(next.check_in, p.timezone)} · {formatTime(next.check_in, p.timezone)} · Unit {next.unit_name}
      </div>
    </div>
  );
}

function Occupancy({ p }: { p: Property }) {
  const s = p.stats;
  const rate = s && s.active_units ? s.occupied_units / s.active_units : 0;
  return (
    <div className="flex items-center gap-2.5">
      <Meter value={rate} className="w-20" label={`${p.name} occupancy`} />
      <span className="text-xs text-muted-foreground tabular">
        <span className="font-medium text-foreground">{Math.round(rate * 100)}%</span> · {s?.occupied_units ?? 0}/{s?.active_units ?? 0}
      </span>
    </div>
  );
}

export function PropertiesPage() {
  const properties = useProperties();
  const org = useCurrentOrganization();
  const create = useCreateProperty();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const canWrite = can(org.data, "properties:write");
  const now = new Date();

  return (
    <div>
      <PageHeader
        title="Properties"
        description="Your portfolio's live operational state."
        actions={
          canWrite ? (
            <Button variant="primary" onClick={() => setOpen(true)}>
              <Plus /> Add property
            </Button>
          ) : null
        }
      />
      <div className="overflow-hidden rounded-lg border bg-surface shadow-xs">
        {properties.isPending ? (
          <SkeletonRows rows={4} columns={6} />
        ) : properties.error ? (
          <ErrorState error={properties.error} onRetry={() => properties.refetch()} />
        ) : properties.data.items.length === 0 ? (
          <EmptyState
            icon={Building2}
            title="No properties yet"
            description="Add your first property, then its units — that's what guests book."
            action={
              canWrite ? (
                <Button size="sm" variant="primary" onClick={() => setOpen(true)}>
                  <Plus /> Add property
                </Button>
              ) : null
            }
          />
        ) : (
          <>
            <TableScroll className="hidden md:block">
              <Table className="min-w-[900px]">
                <THead>
                  <tr>
                    <Th>Property</Th>
                    <Th>Location</Th>
                    <Th>Units</Th>
                    <Th>Occupancy</Th>
                    <Th>Next check-in</Th>
                    <Th>State</Th>
                  </tr>
                </THead>
                <tbody>
                  {properties.data.items.map((p) => (
                    <Tr
                      key={p.id}
                      interactive
                      tabIndex={0}
                      onClick={() => router.push(`/properties/${p.id}`)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") router.push(`/properties/${p.id}`);
                      }}
                      data-testid="property-row"
                    >
                      <Td>
                        <div className="flex items-center gap-3">
                          <span className="grid size-8 shrink-0 place-items-center rounded-md border bg-surface-raised text-muted-foreground" aria-hidden>
                            <Building2 className="size-4" />
                          </span>
                          <div className="min-w-0">
                            <Link href={`/properties/${p.id}`} className="font-medium hover:underline" onClick={(e) => e.stopPropagation()}>
                              {p.name}
                            </Link>
                            <div className="max-w-[280px] truncate text-xs text-muted-foreground">{p.address}</div>
                          </div>
                        </div>
                      </Td>
                      <Td>
                        <div>{cityFromTimezone(p.timezone)}</div>
                        <div className="flex items-center gap-1 text-xs text-muted-foreground tabular">
                          <Clock className="size-3" aria-hidden /> {localClock(p.timezone, now)} local
                        </div>
                      </Td>
                      <Td>
                        <div className="tabular">{p.unit_count}</div>
                        <div className="text-xs text-muted-foreground">
                          {p.stats?.maintenance_units ? <span className="text-warning">{p.stats.maintenance_units} maintenance</span> : `${p.stats?.active_units ?? p.unit_count} active`}
                        </div>
                      </Td>
                      <Td>
                        <Occupancy p={p} />
                      </Td>
                      <Td className="max-w-[240px]">
                        <NextArrival p={p} />
                      </Td>
                      <Td>
                        <PropertyStateBadge p={p} />
                      </Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
            </TableScroll>
            <ul className="divide-y md:hidden">
              {properties.data.items.map((p) => (
                <li key={p.id}>
                  <Link href={`/properties/${p.id}`} className="block space-y-2 px-4 py-3 hover:bg-muted/45">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate text-[13px] font-medium">{p.name}</div>
                        <div className="truncate text-xs text-muted-foreground">
                          {cityFromTimezone(p.timezone)} · {plural(p.unit_count, "unit")}
                        </div>
                      </div>
                      <PropertyStateBadge p={p} />
                    </div>
                    <Occupancy p={p} />
                  </Link>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
      <Sheet open={open} onOpenChange={setOpen} title="Add property" description="Units are added on the property page.">
        <PropertyForm
          submitLabel="Add property"
          pending={create.isPending}
          onCancel={() => setOpen(false)}
          error={create.error instanceof ApiError ? create.error.message : null}
          onSubmit={(values) =>
            create.mutate(values, {
              onSuccess: (p) => {
                toast.success(`${p.name} added`);
                setOpen(false);
              },
            })
          }
        />
      </Sheet>
    </div>
  );
}
