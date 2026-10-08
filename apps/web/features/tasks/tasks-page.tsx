"use client";

import { TASK_PRIORITIES, TASK_STATUSES, TASK_TYPES, TASK_VIEWS, type TaskPriority, type TaskStatus, type TaskType, type TaskView } from "@stayops/shared";
import { ListChecks, Plus } from "lucide-react";
import { useDeferredValue } from "react";

import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/panel";
import { Tabs } from "@/components/ui/segmented";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { can, useCurrentOrganization } from "@/features/organizations/api";
import { plural } from "@/lib/format";
import { useNow } from "@/lib/use-now";
import { useSearchState } from "@/lib/use-search-state";

import { useTaskCounts, useTasks } from "./api";
import { TaskDrawer } from "./components/task-drawer";
import { activeTaskFilters, EMPTY_TASK_FILTERS, TaskFilters, type TaskFilterState } from "./components/task-filters";
import { TaskFormSheet } from "./components/task-form";
import { TaskTable } from "./components/task-table";

const PAGE_SIZE = 50;
const VIEW_LABEL: Record<TaskView, string> = { all: "All", mine: "Mine", today: "Today", overdue: "Overdue", completed: "Completed" };

const pick = <T extends string>(values: readonly T[], raw: string): T | "" => ((values as readonly string[]).includes(raw) ? (raw as T) : "");

export function TasksPage() {
  const { get, set } = useSearchState();
  const org = useCurrentOrganization();
  const canManage = can(org.data, "tasks:manage");
  const view = (pick(TASK_VIEWS, get("view")) || "all") as TaskView;
  const filters: TaskFilterState = {
    q: get("q"),
    propertyId: get("property"),
    unitId: get("unit"),
    assignee: get("assignee"),
    status: pick<TaskStatus>(TASK_STATUSES, get("status")),
    priority: pick<TaskPriority>(TASK_PRIORITIES, get("priority")),
    type: pick<TaskType>(TASK_TYPES, get("type")),
  };
  const page = Math.max(0, Number(get("page")) || 0);
  const q = useDeferredValue(filters.q.trim());
  const openId = get("t") || null;

  const shared = {
    q: q || undefined,
    property_id: filters.propertyId || undefined,
    unit_id: filters.unitId || undefined,
    assignee: filters.assignee || undefined,
    status: filters.status ? [filters.status] : undefined,
    priority: filters.priority ? [filters.priority] : undefined,
    task_type: filters.type ? [filters.type] : undefined,
  };
  const tasks = useTasks({ ...shared, view, limit: PAGE_SIZE, offset: page * PAGE_SIZE });
  const counts = useTaskCounts(shared);

  const setFilters = (next: TaskFilterState) =>
    set({
      q: next.q,
      property: next.propertyId,
      unit: next.unitId,
      assignee: next.assignee,
      status: next.status,
      priority: next.priority,
      type: next.type,
      page: null,
    });
  const total = tasks.data?.total ?? 0;
  const now = useNow();

  return (
    <div data-testid="tasks-page">
      <PageHeader
        title="Tasks"
        description="Every piece of operational work — created automatically from real events, or by your team."
        actions={
          canManage ? (
            <Button variant="primary" onClick={() => set({ new: "1" }, { push: true })}>
              <Plus /> New task
            </Button>
          ) : null
        }
      />
      <div className="overflow-hidden rounded-lg border bg-surface shadow-xs">
        <Tabs
          label="Task views"
          className="px-3"
          value={view}
          onChange={(v) => set({ view: v === "all" ? null : v, page: null })}
          tabs={TASK_VIEWS.map((v) => ({ value: v, label: VIEW_LABEL[v], count: counts.data?.[v] }))}
        />
        <TaskFilters value={filters} onChange={setFilters} />
        {tasks.isPending ? (
          <SkeletonRows rows={8} columns={6} />
        ) : tasks.error ? (
          <ErrorState error={tasks.error} onRetry={() => tasks.refetch()} />
        ) : tasks.data.items.length === 0 ? (
          <EmptyState
            icon={ListChecks}
            title={activeTaskFilters(filters) ? "No tasks match these filters" : view === "overdue" ? "Nothing overdue" : "No tasks here"}
            description={
              activeTaskFilters(filters)
                ? "Try clearing a filter."
                : "Turnover cleanings appear automatically when guests check out."
            }
            action={
              activeTaskFilters(filters) ? (
                <Button size="sm" onClick={() => setFilters(EMPTY_TASK_FILTERS)}>
                  Clear filters
                </Button>
              ) : null
            }
          />
        ) : (
          <div className={tasks.isPlaceholderData ? "opacity-60 transition-opacity" : undefined}>
            <TaskTable rows={tasks.data.items} now={now} onOpen={(id) => set({ t: id }, { push: true })} />
            <div className="flex items-center justify-between border-t px-4 py-2.5 text-xs text-muted-foreground">
              <span className="tabular">
                {total > PAGE_SIZE ? `${page * PAGE_SIZE + 1}–${Math.min((page + 1) * PAGE_SIZE, total)} of ${total}` : plural(total, "task")}
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
      <TaskDrawer id={openId} onClose={() => set({ t: null })} />
      <TaskFormSheet open={get("new") === "1"} onOpenChange={(open) => set({ new: open ? "1" : null })} onCreated={(id) => set({ t: id })} />
    </div>
  );
}
