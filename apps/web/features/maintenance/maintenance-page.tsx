"use client";

import {
  MAINTENANCE_CATEGORIES,
  MAINTENANCE_STATUSES,
  MAINTENANCE_VIEWS,
  type MaintenanceCategory,
  type MaintenanceStatus,
  type MaintenanceView,
  TASK_PRIORITIES,
  type TaskPriority,
  type WorkOrder,
} from "@stayops/shared";
import { Lock, Plus, Wrench, X } from "lucide-react";
import { useDeferredValue } from "react";

import { Button } from "@/components/ui/button";
import { SearchInput, Select } from "@/components/ui/form";
import { Avatar } from "@/components/ui/misc";
import { PageHeader } from "@/components/ui/panel";
import { Tabs } from "@/components/ui/segmented";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { Table, TableScroll, Td, Th, THead, Tr } from "@/components/ui/table";
import { useStaff } from "@/features/operations/api";
import { MaintenanceStatusBadge, PriorityLabel } from "@/features/operations/components/badges";
import { formatAge, MAINTENANCE_CATEGORY_LABEL, MAINTENANCE_STATUS_LABEL, TASK_PRIORITY_LABEL } from "@/features/operations/format";
import { can, useCurrentOrganization } from "@/features/organizations/api";
import { useProperties, useUnits } from "@/features/properties/api";
import { useRecentlyChanged } from "@/features/realtime/realtime-provider";
import { plural } from "@/lib/format";
import { useNow } from "@/lib/use-now";
import { useSearchState } from "@/lib/use-search-state";
import { cn } from "@/lib/utils";

import { useWorkOrderCounts, useWorkOrders } from "./api";
import { WorkOrderDrawer } from "./components/work-order-drawer";
import { WorkOrderFormSheet } from "./components/work-order-form";

const VIEW_LABEL: Record<MaintenanceView, string> = {
  all: "All",
  open: "Open",
  assigned: "Assigned",
  in_progress: "In progress",
  blocked: "Blocked",
  resolved: "Resolved",
};

const pick = <T extends string>(values: readonly T[], raw: string): T | "" => ((values as readonly string[]).includes(raw) ? (raw as T) : "");

function WorkOrderRow({ wo, now, onOpen }: { wo: WorkOrder; now: number; onOpen: (id: string) => void }) {
  const fresh = useRecentlyChanged(wo.id);
  const closed = wo.status === "RESOLVED" || wo.status === "CLOSED";
  const urgent = !closed && (wo.priority === "URGENT" || wo.priority === "HIGH");
  return (
    <Tr
      interactive
      tabIndex={0}
      onClick={() => onOpen(wo.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter") onOpen(wo.id);
      }}
      className={cn(fresh && "animate-row-in", closed && "text-muted-foreground", urgent && wo.priority === "URGENT" && "bg-danger/[0.03]")}
      data-testid="work-order-row"
      data-status={wo.status}
      data-unit-blocked={wo.unit_blocked ? "true" : "false"}
    >
      <Td className="w-[92px]">
        <PriorityLabel priority={wo.priority} />
      </Td>
      <Td className="max-w-[320px]">
        <div className="flex items-center gap-1.5 truncate font-medium">
          {wo.unit_blocked ? <Lock className="size-3.5 shrink-0 text-danger" aria-label="Unit blocked" /> : null}
          <span className="truncate">{wo.title}</span>
        </div>
        <div className="truncate text-xs text-muted-foreground">
          {MAINTENANCE_CATEGORY_LABEL[wo.category]}
          {wo.source_task ? ` · from ${wo.source_task.title}` : ""}
        </div>
      </Td>
      <Td className="max-w-[180px] truncate">{wo.property.name}</Td>
      <Td className="font-medium">{wo.unit.name}</Td>
      <Td className="max-w-[160px]">
        {wo.assignee ? (
          <span className="inline-flex min-w-0 items-center gap-1.5">
            <Avatar name={wo.assignee.name} size="xs" />
            <span className="truncate">{wo.assignee.name}</span>
          </span>
        ) : (
          <span className="text-xs text-subtle-foreground">Unassigned</span>
        )}
      </Td>
      <Td className="text-xs whitespace-nowrap text-muted-foreground tabular" title={wo.created_at}>
        {formatAge(wo.created_at, now)}
      </Td>
      <Td>
        <MaintenanceStatusBadge status={wo.status} />
      </Td>
    </Tr>
  );
}

export function MaintenancePage() {
  const { get, set } = useSearchState();
  const org = useCurrentOrganization();
  const canManage = can(org.data, "maintenance:manage");
  const view = (pick(MAINTENANCE_VIEWS, get("view")) || "all") as MaintenanceView;
  const propertyId = get("property");
  const unitId = get("unit");
  const technician = get("technician");
  const priority = pick<TaskPriority>(TASK_PRIORITIES, get("priority"));
  const category = pick<MaintenanceCategory>(MAINTENANCE_CATEGORIES, get("category"));
  const status = pick<MaintenanceStatus>(MAINTENANCE_STATUSES, get("status"));
  const q = useDeferredValue(get("q").trim());
  const openId = get("wo") || null;
  const properties = useProperties();
  const units = useUnits(propertyId || undefined);
  const staff = useStaff();

  const shared = {
    property_id: propertyId || undefined,
    unit_id: unitId || undefined,
    assignee: technician || undefined,
    priority: priority ? [priority] : undefined,
    category: category ? [category] : undefined,
    status: status ? [status] : undefined,
    q: q || undefined,
  };
  const orders = useWorkOrders({ ...shared, view, limit: 100 });
  const counts = useWorkOrderCounts(shared);
  const filtered = Boolean(propertyId || unitId || technician || priority || category || status || q);
  const now = useNow();
  const clear = () => set({ property: null, unit: null, technician: null, priority: null, category: null, status: null, q: null });

  return (
    <div data-testid="maintenance-page">
      <PageHeader
        title="Maintenance"
        description="Work orders from report to resolution. Blocking issues take the unit out of availability."
        actions={
          <>
            {counts.data?.blocking_units ? (
              <span className="inline-flex items-center gap-1.5 rounded-md border border-danger/25 bg-danger/5 px-2.5 py-1 text-xs font-medium text-danger" data-testid="blocked-units-count">
                <Lock className="size-3.5" aria-hidden /> {plural(counts.data.blocking_units, "unit")} blocked
              </span>
            ) : null}
            {canManage ? (
              <Button variant="primary" onClick={() => set({ new: "1" }, { push: true })}>
                <Plus /> New work order
              </Button>
            ) : null}
          </>
        }
      />
      <div className="overflow-hidden rounded-lg border bg-surface shadow-xs">
        <Tabs
          label="Work order views"
          className="px-3"
          value={view}
          onChange={(v) => set({ view: v === "all" ? null : v })}
          tabs={MAINTENANCE_VIEWS.map((v) => ({ value: v, label: VIEW_LABEL[v], count: counts.data?.[v] }))}
        />
        <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
          <SearchInput
            wrapperClassName="w-full sm:w-52"
            placeholder="Issue or unit…"
            value={get("q")}
            onChange={(e) => set({ q: e.target.value })}
            onClear={() => set({ q: null })}
            aria-label="Search work orders"
          />
          <Select wrapperClassName="w-44" value={propertyId} onChange={(e) => set({ property: e.target.value, unit: null })} aria-label="Filter by property">
            <option value="">All properties</option>
            {properties.data?.items.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </Select>
          <Select wrapperClassName="w-28" value={unitId} onChange={(e) => set({ unit: e.target.value })} aria-label="Filter by unit" disabled={!propertyId}>
            <option value="">{propertyId ? "All units" : "Unit"}</option>
            {units.data?.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name}
              </option>
            ))}
          </Select>
          <Select wrapperClassName="w-40" value={technician} onChange={(e) => set({ technician: e.target.value })} aria-label="Filter by technician">
            <option value="">Any technician</option>
            <option value="unassigned">Unassigned</option>
            {staff.data?.map((m) => (
              <option key={m.user_id} value={m.user_id}>
                {m.name}
              </option>
            ))}
          </Select>
          <Select wrapperClassName="w-32" value={priority} onChange={(e) => set({ priority: e.target.value })} aria-label="Filter by priority">
            <option value="">Any priority</option>
            {TASK_PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {TASK_PRIORITY_LABEL[p]}
              </option>
            ))}
          </Select>
          <Select wrapperClassName="w-32" value={category} onChange={(e) => set({ category: e.target.value })} aria-label="Filter by category">
            <option value="">Any category</option>
            {MAINTENANCE_CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {MAINTENANCE_CATEGORY_LABEL[c]}
              </option>
            ))}
          </Select>
          <Select wrapperClassName="w-32" value={status} onChange={(e) => set({ status: e.target.value })} aria-label="Filter by status">
            <option value="">Any status</option>
            {MAINTENANCE_STATUSES.map((s) => (
              <option key={s} value={s}>
                {MAINTENANCE_STATUS_LABEL[s]}
              </option>
            ))}
          </Select>
          {filtered ? (
            <Button variant="ghost" size="sm" onClick={clear} className="text-muted-foreground">
              <X /> Clear
            </Button>
          ) : null}
        </div>
        {orders.isPending ? (
          <SkeletonRows rows={6} columns={6} />
        ) : orders.error ? (
          <ErrorState error={orders.error} onRetry={() => orders.refetch()} />
        ) : orders.data.items.length === 0 ? (
          <EmptyState
            icon={Wrench}
            title={filtered ? "No work orders match these filters" : "No work orders here"}
            description={filtered ? "Try clearing a filter." : "Issues reported by your team or found during cleanings appear here."}
            action={
              filtered ? (
                <Button size="sm" onClick={clear}>
                  Clear filters
                </Button>
              ) : null
            }
          />
        ) : (
          <div className={orders.isPlaceholderData ? "opacity-60 transition-opacity" : undefined}>
            <TableScroll>
              <Table className="min-w-[900px]">
                <THead>
                  <tr>
                    <Th>Priority</Th>
                    <Th>Issue</Th>
                    <Th>Property</Th>
                    <Th>Unit</Th>
                    <Th>Technician</Th>
                    <Th>Age</Th>
                    <Th>Status</Th>
                  </tr>
                </THead>
                <tbody>
                  {orders.data.items.map((wo) => (
                    <WorkOrderRow key={wo.id} wo={wo} now={now} onOpen={(id) => set({ wo: id }, { push: true })} />
                  ))}
                </tbody>
              </Table>
            </TableScroll>
            <div className="border-t px-4 py-2.5 text-xs text-muted-foreground">{plural(orders.data.total, "work order")}</div>
          </div>
        )}
      </div>
      <WorkOrderDrawer id={openId} onClose={() => set({ wo: null })} />
      <WorkOrderFormSheet open={get("new") === "1"} onOpenChange={(open) => set({ new: open ? "1" : null })} canAssign={canManage} onCreated={(id) => set({ wo: id })} />
    </div>
  );
}
