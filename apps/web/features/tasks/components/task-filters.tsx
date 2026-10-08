"use client";

import { TASK_PRIORITIES, TASK_STATUSES, TASK_TYPES, type TaskPriority, type TaskStatus, type TaskType } from "@stayops/shared";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SearchInput, Select } from "@/components/ui/form";
import { useStaff } from "@/features/operations/api";
import { TASK_PRIORITY_LABEL, TASK_STATUS_LABEL, TASK_TYPE_LABEL } from "@/features/operations/format";
import { useProperties, useUnits } from "@/features/properties/api";

export type TaskFilterState = {
  q: string;
  propertyId: string;
  unitId: string;
  assignee: string;
  status: TaskStatus | "";
  priority: TaskPriority | "";
  type: TaskType | "";
};

export const EMPTY_TASK_FILTERS: TaskFilterState = { q: "", propertyId: "", unitId: "", assignee: "", status: "", priority: "", type: "" };

export const activeTaskFilters = (f: TaskFilterState) =>
  [f.q, f.propertyId, f.unitId, f.assignee, f.status, f.priority, f.type].filter(Boolean).length;

export function TaskFilters({
  value,
  onChange,
  showAssignee = true,
  types = TASK_TYPES,
}: {
  value: TaskFilterState;
  onChange: (next: TaskFilterState) => void;
  showAssignee?: boolean;
  types?: readonly TaskType[];
}) {
  const properties = useProperties();
  const units = useUnits(value.propertyId || undefined);
  const staff = useStaff({ enabled: showAssignee });
  const set = (patch: Partial<TaskFilterState>) => onChange({ ...value, ...patch });
  const active = activeTaskFilters(value);
  return (
    <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
      <SearchInput
        wrapperClassName="w-full sm:w-56"
        placeholder="Task, guest, code or unit…"
        value={value.q}
        onChange={(e) => set({ q: e.target.value })}
        onClear={() => set({ q: "" })}
        aria-label="Search tasks"
      />
      <Select wrapperClassName="w-44" value={value.propertyId} onChange={(e) => set({ propertyId: e.target.value, unitId: "" })} aria-label="Filter by property">
        <option value="">All properties</option>
        {properties.data?.items.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </Select>
      <Select wrapperClassName="w-28" value={value.unitId} onChange={(e) => set({ unitId: e.target.value })} aria-label="Filter by unit" disabled={!value.propertyId}>
        <option value="">{value.propertyId ? "All units" : "Unit"}</option>
        {units.data?.map((u) => (
          <option key={u.id} value={u.id}>
            {u.name}
          </option>
        ))}
      </Select>
      {showAssignee ? (
        <Select wrapperClassName="w-40" value={value.assignee} onChange={(e) => set({ assignee: e.target.value })} aria-label="Filter by assignee">
          <option value="">Anyone</option>
          <option value="unassigned">Unassigned</option>
          <option value="me">Assigned to me</option>
          {staff.data?.map((m) => (
            <option key={m.user_id} value={m.user_id}>
              {m.name}
            </option>
          ))}
        </Select>
      ) : null}
      <Select wrapperClassName="w-32" value={value.status} onChange={(e) => set({ status: e.target.value as TaskStatus | "" })} aria-label="Filter by status">
        <option value="">Any status</option>
        {TASK_STATUSES.map((s) => (
          <option key={s} value={s}>
            {TASK_STATUS_LABEL[s]}
          </option>
        ))}
      </Select>
      <Select wrapperClassName="w-32" value={value.priority} onChange={(e) => set({ priority: e.target.value as TaskPriority | "" })} aria-label="Filter by priority">
        <option value="">Any priority</option>
        {TASK_PRIORITIES.map((p) => (
          <option key={p} value={p}>
            {TASK_PRIORITY_LABEL[p]}
          </option>
        ))}
      </Select>
      {types.length > 1 ? (
        <Select wrapperClassName="w-40" value={value.type} onChange={(e) => set({ type: e.target.value as TaskType | "" })} aria-label="Filter by type">
          <option value="">Any type</option>
          {types.map((t) => (
            <option key={t} value={t}>
              {TASK_TYPE_LABEL[t]}
            </option>
          ))}
        </Select>
      ) : null}
      {active ? (
        <Button variant="ghost" size="sm" onClick={() => onChange(EMPTY_TASK_FILTERS)} className="text-muted-foreground">
          <X /> Clear {active > 1 ? `(${active})` : ""}
        </Button>
      ) : null}
    </div>
  );
}
