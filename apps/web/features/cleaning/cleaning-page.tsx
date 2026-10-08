"use client";

import { CLEANING_VIEWS, type CleaningView } from "@stayops/shared";
import { ArrowRight, SearchX, Sparkles } from "lucide-react";
import Link from "next/link";
import { useDeferredValue } from "react";

import { Button } from "@/components/ui/button";
import { SearchInput, Select } from "@/components/ui/form";
import { PageHeader } from "@/components/ui/panel";
import { Tabs } from "@/components/ui/segmented";
import { Sheet } from "@/components/ui/sheet";
import { EmptyState, ErrorState, Skeleton, SkeletonRows } from "@/components/ui/states";
import { useStaff } from "@/features/operations/api";
import { CleaningStatusBadge, PriorityLabel } from "@/features/operations/components/badges";
import { EntityTimeline } from "@/features/operations/components/entity-timeline";
import { formatTurnover } from "@/features/operations/format";
import { useProperties, useUnits } from "@/features/properties/api";
import { TaskActions, useTaskPermissions } from "@/features/tasks/components/task-drawer";
import { ApiError } from "@/lib/api/client";
import { formatInZone, plural } from "@/lib/format";
import { useNow } from "@/lib/use-now";
import { useSearchState } from "@/lib/use-search-state";
import { cn } from "@/lib/utils";

import { useCleaning, useCleaningCounts, useCleanings } from "./api";
import { CleaningBoard, TurnoverWindow } from "./components/cleaning-board";
import { CleaningPanel } from "./components/cleaning-panel";

const VIEW_LABEL: Record<CleaningView, string> = { today: "Today", upcoming: "Upcoming", inspection: "Inspection", completed: "Completed" };

function CleaningDetail({ id }: { id: string }) {
  const query = useCleaning(id);
  const cleaning = query.data;
  const { canManage, canWork } = useTaskPermissions(cleaning?.task);
  if (query.isPending) return <Skeleton className="m-5 h-60" />;
  if (query.error || !cleaning) {
    if (query.error instanceof ApiError && query.error.status === 404) {
      return <EmptyState icon={SearchX} title="Cleaning not found" description="It may not be assigned to you." />;
    }
    return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  }
  const tz = cleaning.task.property.timezone;
  const t = cleaning.turnover;
  return (
    <div data-testid="cleaning-detail" data-status={cleaning.status}>
      <header className="border-b px-5 pt-5 pb-4">
        <div className="pr-8">
          <p className="text-xs text-muted-foreground">{cleaning.task.property.name}</p>
          <h2 className="text-lg font-semibold">{cleaning.task.unit ? `Unit ${cleaning.task.unit.name}` : cleaning.task.title}</h2>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <CleaningStatusBadge status={cleaning.status} />
            <PriorityLabel priority={cleaning.task.priority} />
            <Link href={`/tasks?t=${cleaning.task.id}`} className="text-xs text-accent hover:underline">
              Open task
            </Link>
          </div>
        </div>
        <TaskActions task={cleaning.task} canManage={canManage} canWork={canWork} />
      </header>
      <section className="border-b px-5 py-4">
        <div
          className={cn(
            "grid grid-cols-[1fr_auto_1fr] items-center gap-3 rounded-md border px-3 py-2.5",
            t.tight ? "border-danger/30 bg-danger/5" : "bg-surface-raised",
          )}
        >
          <div>
            <div className="text-[11px] text-muted-foreground">Checkout</div>
            <div className="text-[13px] font-medium tabular">{t.checkout_at ? formatInZone(t.checkout_at, tz, { hour: "2-digit", minute: "2-digit" }) : "—"}</div>
            <div className="truncate text-xs text-muted-foreground">{cleaning.departure?.guest_name ?? "—"}</div>
          </div>
          <div className={cn("text-center text-xs font-medium", t.tight ? "text-danger" : "text-muted-foreground")} data-testid="turnover-summary">
            <ArrowRight className="mx-auto mb-0.5 size-3.5" aria-hidden />
            {formatTurnover(t.minutes)}
          </div>
          <div className="text-right">
            <div className="text-[11px] text-muted-foreground">Next check-in</div>
            <div className="text-[13px] font-medium tabular">
              {t.next_check_in_at ? formatInZone(t.next_check_in_at, tz, { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "—"}
            </div>
            <div className="truncate text-xs text-muted-foreground">{cleaning.next_arrival?.guest_name ?? "No arrival booked"}</div>
          </div>
        </div>
        {t.tight ? (
          <p className="mt-2 text-xs text-danger">Tight turnover: under {t.threshold_minutes / 60}h between departure and arrival.</p>
        ) : null}
      </section>
      <section className="border-b px-5 py-4">
        <CleaningPanel cleaning={cleaning} canWork={canWork} canManage={canManage} />
      </section>
      <section className="px-5 py-4">
        <h3 className="eyebrow mb-2.5">Timeline</h3>
        <EntityTimeline entityIds={[cleaning.id, cleaning.task.id]} tz={tz} />
      </section>
    </div>
  );
}

export function CleaningPage() {
  const { get, set } = useSearchState();
  const view = ((CLEANING_VIEWS as readonly string[]).includes(get("view")) ? get("view") : "today") as CleaningView;
  const propertyId = get("property");
  const unitId = get("unit");
  const assignee = get("cleaner");
  const q = useDeferredValue(get("q").trim());
  const openId = get("c") || null;
  const properties = useProperties();
  const units = useUnits(propertyId || undefined);
  const staff = useStaff();
  const shared = {
    property_id: propertyId || undefined,
    unit_id: unitId || undefined,
    assignee: assignee || undefined,
    q: q || undefined,
  };
  const cleanings = useCleanings({ ...shared, view, limit: 100 });
  const counts = useCleaningCounts(shared);
  const tight = cleanings.data?.items.filter((c) => c.turnover.tight && c.status !== "APPROVED").length ?? 0;
  const now = useNow();

  return (
    <div data-testid="cleaning-page">
      <PageHeader
        title="Cleaning"
        description="Turnovers between departures and arrivals, with checklists and inspection."
        actions={
          tight ? (
            <span className="rounded-md border border-danger/25 bg-danger/5 px-2.5 py-1 text-xs font-medium text-danger" data-testid="tight-count">
              {plural(tight, "tight turnover")}
            </span>
          ) : null
        }
      />
      <div className="overflow-hidden rounded-lg border bg-surface shadow-xs">
        <Tabs
          label="Cleaning views"
          className="px-3"
          value={view}
          onChange={(v) => set({ view: v === "today" ? null : v })}
          tabs={CLEANING_VIEWS.map((v) => ({ value: v, label: VIEW_LABEL[v], count: counts.data?.[v] }))}
        />
        <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
          <SearchInput
            wrapperClassName="w-full sm:w-56"
            placeholder="Unit, guest or code…"
            value={get("q")}
            onChange={(e) => set({ q: e.target.value })}
            onClear={() => set({ q: null })}
            aria-label="Search cleanings"
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
          <Select wrapperClassName="w-40" value={assignee} onChange={(e) => set({ cleaner: e.target.value })} aria-label="Filter by cleaner">
            <option value="">Any cleaner</option>
            <option value="unassigned">Unassigned</option>
            {staff.data?.map((m) => (
              <option key={m.user_id} value={m.user_id}>
                {m.name}
              </option>
            ))}
          </Select>
        </div>
        {cleanings.isPending ? (
          <SkeletonRows rows={6} columns={6} />
        ) : cleanings.error ? (
          <ErrorState error={cleanings.error} onRetry={() => cleanings.refetch()} />
        ) : cleanings.data.items.length === 0 ? (
          <EmptyState
            icon={Sparkles}
            title={view === "today" ? "No turnovers today" : view === "inspection" ? "Nothing awaiting inspection" : "No cleanings here"}
            description="Turnover cleanings are created automatically when a guest checks out."
            action={
              propertyId || unitId || assignee || q ? (
                <Button size="sm" onClick={() => set({ property: null, unit: null, cleaner: null, q: null })}>
                  Clear filters
                </Button>
              ) : null
            }
          />
        ) : (
          <div className={cleanings.isPlaceholderData ? "opacity-60 transition-opacity" : undefined}>
            <CleaningBoard rows={cleanings.data.items} now={now} onOpen={(id) => set({ c: id }, { push: true })} />
            <div className="border-t px-4 py-2.5 text-xs text-muted-foreground">{plural(cleanings.data.total, "cleaning")}</div>
          </div>
        )}
      </div>
      <Sheet open={Boolean(openId)} onOpenChange={(open) => !open && set({ c: null })} title="Cleaning" size="lg" hideTitle bodyClassName="p-0">
        {openId ? <CleaningDetail id={openId} /> : null}
      </Sheet>
    </div>
  );
}

export { TurnoverWindow };
