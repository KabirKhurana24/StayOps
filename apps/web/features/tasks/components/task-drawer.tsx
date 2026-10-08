"use client";

import { TASK_PRIORITIES, TASK_TRANSITIONS, type Task, type TaskPriority } from "@stayops/shared";
import { Ban, Bot, CheckCircle2, Flag, MoreHorizontal, Play, SearchX } from "lucide-react";
import Link from "next/link";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { Avatar } from "@/components/ui/misc";
import { Sheet } from "@/components/ui/sheet";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { useEvents } from "@/features/activity/api";
import { useCleaning } from "@/features/cleaning/api";
import { CleaningPanel } from "@/features/cleaning/components/cleaning-panel";
import { AssigneeMenu } from "@/features/operations/components/assignee-menu";
import { DueLabel, PriorityLabel, TaskStatusBadge } from "@/features/operations/components/badges";
import { EntityTimeline } from "@/features/operations/components/entity-timeline";
import { TASK_PRIORITY_LABEL, TASK_TYPE_LABEL } from "@/features/operations/format";
import { can, useCurrentOrganization, useMe } from "@/features/organizations/api";
import { useRecentlyChanged } from "@/features/realtime/realtime-provider";
import { ApiError } from "@/lib/api/client";
import { formatFull, formatInZone } from "@/lib/format";
import { useNow } from "@/lib/use-now";
import { cn } from "@/lib/utils";

import { useAssignTask, useTask, useTaskAction, useUpdateTask } from "../api";

function Section({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="border-b px-5 py-4 last:border-0">
      <div className="mb-2.5 flex items-center justify-between">
        <h3 className="eyebrow">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[112px_minmax(0,1fr)] gap-3 py-1 text-[13px]">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

/** Lifecycle + ownership actions; the server re-checks every permission and transition. */
export function useTaskPermissions(task: Task | undefined) {
  const org = useCurrentOrganization();
  const me = useMe();
  const canManage = can(org.data, "tasks:manage");
  const mine = Boolean(task?.assignee && me.data && task.assignee.id === me.data.id);
  const canWork = canManage || (can(org.data, "tasks:work") && mine);
  return { canManage, canWork, mine };
}

export function TaskActions({ task, canManage, canWork }: { task: Task; canManage: boolean; canWork: boolean }) {
  const action = useTaskAction(task.id);
  const assign = useAssignTask(task.id);
  const update = useUpdateTask(task.id);
  const [cancelling, setCancelling] = useState(false);
  const next = TASK_TRANSITIONS[task.status];
  const run = (name: "start" | "complete" | "cancel", done: string) =>
    action.mutate(
      { action: name, expectedVersion: task.version },
      {
        onSuccess: () => {
          setCancelling(false);
          toast.success(`${done}: ${task.title}`);
        },
      },
    );
  return (
    <div className="mt-4 flex flex-wrap items-center gap-2">
      {canWork && next.includes("IN_PROGRESS") ? (
        <Button size="sm" variant="primary" disabled={action.isPending} onClick={() => run("start", "Started")}>
          <Play /> {task.task_type === "TURNOVER_CLEANING" ? "Start cleaning" : "Start"}
        </Button>
      ) : null}
      {canWork && next.includes("COMPLETED") ? (
        <Button
          size="sm"
          variant={task.status === "IN_PROGRESS" ? "primary" : "secondary"}
          disabled={action.isPending}
          onClick={() => run("complete", "Completed")}
        >
          <CheckCircle2 /> {task.task_type === "TURNOVER_CLEANING" ? "Complete cleaning" : "Complete"}
        </Button>
      ) : null}
      {canManage && next.length ? (
        <AssigneeMenu
          value={task.assignee?.id ?? null}
          pending={assign.isPending}
          onChange={(id) =>
            assign.mutate(id, {
              onSuccess: (t) => toast.success(t.assignee ? `Assigned to ${t.assignee.name}` : "Unassigned"),
            })
          }
        />
      ) : null}
      {canManage && next.length ? (
        <Menu>
          <MenuTrigger asChild>
            <Button size="icon-sm" aria-label="More task actions">
              <MoreHorizontal />
            </Button>
          </MenuTrigger>
          <MenuContent className="w-52">
            <MenuLabel>Priority</MenuLabel>
            {TASK_PRIORITIES.map((p: TaskPriority) => (
              <MenuItem
                key={p}
                icon={<Flag />}
                disabled={p === task.priority || update.isPending}
                onSelect={() => update.mutate({ priority: p, expected_version: task.version })}
              >
                {TASK_PRIORITY_LABEL[p]}
              </MenuItem>
            ))}
            <MenuSeparator />
            <MenuItem tone="danger" icon={<Ban />} onSelect={() => setCancelling(true)}>
              Cancel task
            </MenuItem>
          </MenuContent>
        </Menu>
      ) : null}
      <ConfirmDialog
        open={cancelling}
        onOpenChange={setCancelling}
        title="Cancel this task?"
        description={`${task.title} · ${task.property.name}${task.unit ? ` · Unit ${task.unit.name}` : ""}. The change is recorded in the audit trail.`}
        confirmLabel="Cancel task"
        cancelLabel="Keep task"
        pending={action.isPending}
        onConfirm={() => run("cancel", "Cancelled")}
      />
    </div>
  );
}

function RelatedEvents({ ids }: { ids: string[] }) {
  const a = useEvents({ aggregate_id: ids[0], limit: 20 });
  const b = useEvents({ aggregate_id: ids[1] ?? ids[0], limit: 20 });
  const events = [...(a.data?.items ?? []), ...(ids[1] ? (b.data?.items ?? []) : [])].sort(
    (x, y) => Date.parse(y.occurred_at) - Date.parse(x.occurred_at),
  );
  if (!events.length) return <p className="text-xs text-subtle-foreground">No events yet.</p>;
  return (
    <ul className="space-y-1" data-testid="related-events">
      {events.map((e) => (
        <li key={e.id} className="flex items-center justify-between gap-2 text-xs">
          <span className="font-mono text-[11px]">{e.event_type}</span>
          <span className="flex items-center gap-1.5 text-muted-foreground">
            {e.source === "worker" ? <Bot className="size-3" aria-label="Emitted by automation" /> : null}
            <Badge tone={e.status === "COMPLETED" ? "success" : e.status === "FAILED" ? "danger" : "neutral"}>{e.status.toLowerCase()}</Badge>
          </span>
        </li>
      ))}
    </ul>
  );
}

export function TaskDetail({ task }: { task: Task }) {
  const { canManage, canWork } = useTaskPermissions(task);
  const cleaning = useCleaning(task.cleaning?.id);
  const fresh = useRecentlyChanged(task.id);
  const now = useNow();
  const tz = task.property.timezone;
  const done = task.status === "COMPLETED" || task.status === "CANCELLED";
  return (
    <div className="flex min-h-full flex-col" data-testid="task-detail" data-status={task.status}>
      <header className={cn("border-b px-5 pt-5 pb-4", fresh && "animate-row-in")}>
        <div className="pr-8">
          <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
            <span>{TASK_TYPE_LABEL[task.task_type]}</span>
            {task.automated ? (
              <span className="inline-flex items-center gap-1">
                · <Bot className="size-3" aria-hidden /> created by automation
              </span>
            ) : null}
          </div>
          <h2 className="text-lg leading-tight font-semibold">{task.title}</h2>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <TaskStatusBadge status={task.status} />
            <PriorityLabel priority={task.priority} />
            <DueLabel dueAt={task.due_at} now={now} done={done} />
          </div>
        </div>
        {canManage || canWork ? <TaskActions task={task} canManage={canManage} canWork={canWork} /> : null}
      </header>

      <Section title="Details">
        <dl>
          <Row label="Property">
            <Link className="text-accent hover:underline" href={`/properties/${task.property.id}`}>
              {task.property.name}
            </Link>
          </Row>
          <Row label="Unit">{task.unit ? task.unit.name : <span className="text-subtle-foreground">Whole property</span>}</Row>
          <Row label="Reservation">
            {task.reservation ? (
              <Link className="text-accent hover:underline" href={`/reservations?r=${task.reservation.id}`}>
                {task.reservation.guest_name} · <span className="font-mono">{task.reservation.confirmation_code}</span>
              </Link>
            ) : (
              <span className="text-subtle-foreground">None</span>
            )}
          </Row>
          <Row label="Assignee">
            {task.assignee ? (
              <span className="inline-flex items-center gap-1.5" data-testid="detail-assignee">
                <Avatar name={task.assignee.name} size="xs" /> {task.assignee.name}
              </span>
            ) : (
              <span className="text-subtle-foreground" data-testid="detail-assignee">
                Unassigned
              </span>
            )}
          </Row>
          <Row label="Priority">{TASK_PRIORITY_LABEL[task.priority]}</Row>
          <Row label="Due">{task.due_at ? formatFull(task.due_at, tz) : <span className="text-subtle-foreground">No due time</span>}</Row>
          {task.started_at ? <Row label="Started">{formatInZone(task.started_at, tz)}</Row> : null}
          {task.completed_at ? <Row label="Completed">{formatInZone(task.completed_at, tz)}</Row> : null}
          <Row label="Created by">{task.created_by_label ?? "—"}</Row>
        </dl>
        {task.description ? <p className="mt-2 rounded-md border bg-surface-raised px-3 py-2 text-[13px] whitespace-pre-wrap">{task.description}</p> : null}
      </Section>

      {task.cleaning ? (
        <Section
          title="Cleaning"
          action={
            <Link href={`/cleaning?c=${task.cleaning.id}`} className="text-xs text-accent hover:underline">
              Turnover view
            </Link>
          }
        >
          {cleaning.data ? (
            <CleaningPanel cleaning={cleaning.data} canWork={canWork} canManage={canManage} />
          ) : cleaning.error ? (
            <ErrorState compact error={cleaning.error} onRetry={() => cleaning.refetch()} />
          ) : (
            <Skeleton className="h-40 w-full" />
          )}
        </Section>
      ) : null}

      <Section title="Timeline">
        <EntityTimeline entityIds={[task.id, task.cleaning?.id]} tz={tz} />
      </Section>
      <Section title="Related events">
        <RelatedEvents ids={[task.id, ...(task.cleaning ? [task.cleaning.id] : [])]} />
      </Section>
    </div>
  );
}

function DrawerBody({ id }: { id: string }) {
  const task = useTask(id);
  if (task.isPending) {
    return (
      <div className="space-y-4 p-5" aria-busy="true" aria-label="Loading task">
        <Skeleton className="h-5 w-56" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (task.error) {
    if (task.error instanceof ApiError && task.error.status === 404) {
      return <EmptyState icon={SearchX} title="Task not found" description="It may belong to another organization or not be assigned to you." />;
    }
    return <ErrorState error={task.error} onRetry={() => task.refetch()} />;
  }
  return <TaskDetail task={task.data} />;
}

export function TaskDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  return (
    <Sheet open={Boolean(id)} onOpenChange={(open) => !open && onClose()} title="Task details" size="lg" hideTitle bodyClassName="p-0">
      {id ? <DrawerBody id={id} /> : null}
    </Sheet>
  );
}
