"use client";

import { RESERVATION_SOURCES, RESERVATION_STATUSES, type ReservationSource, type ReservationStatus } from "@stayops/shared";
import { ChevronDown, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input, SearchInput, Select } from "@/components/ui/form";
import { Menu, MenuCheckboxItem, MenuContent, MenuLabel, MenuTrigger } from "@/components/ui/menu";
import { useProperties, useUnits } from "@/features/properties/api";
import { SOURCE_LABEL, STATUS_LABEL } from "@/lib/format";
import { cn } from "@/lib/utils";

export type ReservationFilterState = {
  q: string;
  statuses: ReservationStatus[];
  source: ReservationSource | "";
  propertyId: string;
  unitId: string;
  from: string;
  to: string;
};

export const EMPTY_FILTERS: ReservationFilterState = { q: "", statuses: [], source: "", propertyId: "", unitId: "", from: "", to: "" };

export function activeFilterCount(f: ReservationFilterState): number {
  return [f.q, f.statuses.length, f.source, f.propertyId, f.unitId, f.from || f.to].filter(Boolean).length;
}

function StatusFilter({ value, onChange }: { value: ReservationStatus[]; onChange: (v: ReservationStatus[]) => void }) {
  const label = value.length === 0 ? "Any status" : value.length === 1 ? STATUS_LABEL[value[0] as ReservationStatus] : `${value.length} statuses`;
  return (
    <Menu>
      <MenuTrigger asChild>
        <Button className={cn("justify-between font-normal", value.length && "border-accent/40 bg-accent-soft/50")} aria-label="Filter by status">
          <span className={value.length ? "text-foreground" : "text-muted-foreground"}>{label}</span>
          <ChevronDown className="text-subtle-foreground" />
        </Button>
      </MenuTrigger>
      <MenuContent align="start" className="w-48">
        <MenuLabel>Status</MenuLabel>
        {RESERVATION_STATUSES.map((s) => (
          <MenuCheckboxItem
            key={s}
            checked={value.includes(s)}
            onCheckedChange={(checked) => onChange(checked ? [...value, s] : value.filter((x) => x !== s))}
          >
            {STATUS_LABEL[s]}
          </MenuCheckboxItem>
        ))}
      </MenuContent>
    </Menu>
  );
}

export function ReservationFilters({ value, onChange }: { value: ReservationFilterState; onChange: (next: ReservationFilterState) => void }) {
  const properties = useProperties();
  const units = useUnits(value.propertyId || undefined);
  const set = (patch: Partial<ReservationFilterState>) => onChange({ ...value, ...patch });
  const active = activeFilterCount(value);

  return (
    <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
      <SearchInput
        wrapperClassName="w-full sm:w-60"
        placeholder="Guest, email or code…"
        value={value.q}
        onChange={(e) => set({ q: e.target.value })}
        onClear={() => set({ q: "" })}
        aria-label="Search reservations"
      />
      <div className="flex items-center gap-1.5" role="group" aria-label="Stay dates">
        <Input type="date" value={value.from} max={value.to || undefined} onChange={(e) => set({ from: e.target.value })} aria-label="Stays from" className="w-[138px]" />
        <span className="text-xs text-subtle-foreground">–</span>
        <Input type="date" value={value.to} min={value.from || undefined} onChange={(e) => set({ to: e.target.value })} aria-label="Stays until" className="w-[138px]" />
      </div>
      <Select
        wrapperClassName="w-44"
        value={value.propertyId}
        onChange={(e) => set({ propertyId: e.target.value, unitId: "" })}
        aria-label="Filter by property"
      >
        <option value="">All properties</option>
        {properties.data?.items.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </Select>
      <Select wrapperClassName="w-32" value={value.unitId} onChange={(e) => set({ unitId: e.target.value })} aria-label="Filter by unit" disabled={!value.propertyId}>
        <option value="">{value.propertyId ? "All units" : "Unit"}</option>
        {units.data?.map((u) => (
          <option key={u.id} value={u.id}>
            {u.name}
          </option>
        ))}
      </Select>
      <StatusFilter value={value.statuses} onChange={(statuses) => set({ statuses })} />
      <Select wrapperClassName="w-36" value={value.source} onChange={(e) => set({ source: e.target.value as ReservationSource | "" })} aria-label="Filter by source">
        <option value="">Any source</option>
        {RESERVATION_SOURCES.map((s) => (
          <option key={s} value={s}>
            {SOURCE_LABEL[s]}
          </option>
        ))}
      </Select>
      {active ? (
        <Button variant="ghost" size="sm" onClick={() => onChange(EMPTY_FILTERS)} className="text-muted-foreground">
          <X /> Clear {active > 1 ? `(${active})` : ""}
        </Button>
      ) : null}
    </div>
  );
}
