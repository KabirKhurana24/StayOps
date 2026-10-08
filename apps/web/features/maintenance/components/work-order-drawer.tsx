"use client";

import { MAINTENANCE_TRANSITIONS, type WorkOrder } from "@stayops/shared";
import { CheckCircle2, Lock, LockOpen, MoreHorizontal, Pause, Play, SearchX, Sparkles, Users } from "lucide-react";
import Link from "next/link";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/form";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/menu";
import { Avatar } from "@/components/ui/misc";
import { Sheet } from "@/components/ui/sheet";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { AssigneeMenu } from "@/features/operations/components/assignee-menu";
import { MaintenanceStatusBadge, PriorityLabel } from "@/features/operations/components/badges";
import { EntityTimeline } from "@/features/operations/components/entity-timeline";
import { formatAge, MAINTENANCE_CATEGORY_LABEL, TASK_TYPE_LABEL } from "@/features/operations/format";
import { can, useCurrentOrganization, useMe } from "@/features/organizations/api";
import { useRecentlyChanged } from "@/features/realtime/realtime-provider";
import { ApiError } from "@/lib/api/client";
import { formatFull, formatStayRange } from "@/lib/format";
import { useNow } from "@/lib/use-now";
import { cn } from "@/lib/utils";

import { useAssignWorkOrder, useUpdateWorkOrder, useWorkOrder, useWorkOrderAction, type WorkOrderActionName } from "../api";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-b px-5 py-4 last:border-0">
      <h3 className="eyebrow mb-2.5">{title}</h3>
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

export function useWorkOrderPermissions(order: WorkOrder | undefined) {
  const org = useCurrentOrganization();
  const me = useMe();
  const canManage = can(org.data, "maintenance:manage");
  const mine = Boolean(order?.assignee && me.data && order.assignee.id === me.data.id);
  return { canManage, canWork: canManage || (can(org.data, "maintenance:work") && mine) };
}

const ACTION_COPY: Record<WorkOrderActionName, string> = { start: "Started", block: "On hold", resolve: "Resolved", close: "Closed" };

function Actions({ order, canManage, canWork }: { order: WorkOrder; canManage: boolean; canWork: boolean }) {
  const action = useWorkOrderAction(order.id);
  const assign = useAssignWorkOrder(order.id);
  const update = useUpdateWorkOrder(order.id);
  const [prompt, setPrompt] = useState<"block" | "resolve" | null>(null);
  const [notes, setNotes] = useState("");
  const next = MAINTENANCE_TRANSITIONS[order.status];
  const run = (name: WorkOrderActionName, text?: string) =>
    action.mutate(
      { action: name, notes: text },
      {
        onSuccess: (wo) => {
          setPrompt(null);
          setNotes("");
          toast.success(`${ACTION_COPY[name]}: ${wo.title}`, {
            description: name === "resolve" && order.unit_blocked ? `Unit ${wo.unit.name} is bookable again` : undefined,
          });
        },
      },
    );
  return (
    <div className="mt-4 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {canWork && next.includes("IN_PROGRESS") ? (
          <Button size="sm" variant="primary" disabled={action.isPending} onClick={() => run("start")}>
            <Play /> {order.status === "RESOLVED" ? "Reopen" : order.status === "BLOCKED" ? "Resume" : "Start"}
          </Button>
        ) : null}
        {canWork && next.includes("BLOCKED") ? (
          <Button size="sm" disabled={action.isPending} onClick={() => setPrompt("block")}>
            <Pause /> Block
          </Button>
        ) : null}
        {canWork && next.includes("RESOLVED") ? (
          <Button size="sm" variant={order.status === "IN_PROGRESS" ? "primary" : "secondary"} disabled={action.isPending} onClick={() => setPrompt("resolve")}>
            <CheckCircle2 /> Resolve
          </Button>
        ) : null}
        {canManage && order.status !== "RESOLVED" && order.status !== "CLOSED" ? (
          <AssigneeMenu
            workload="work_orders"
            label="Assign technician"
            value={order.assignee?.id ?? null}
            pending={assign.isPending}
            onChange={(id) => assign.mutate(id, { onSuccess: (wo) => toast.success(wo.assignee ? `Assigned to ${wo.assignee.name}` : "Unassigned") })}
          />
        ) : null}
        {canManage && next.includes("CLOSED") ? (
          <Button size="sm" variant={order.status === "RESOLVED" ? "primary" : "ghost"} disabled={action.isPending} onClick={() => run("close")}>
            Close
          </Button>
        ) : null}
        {canManage && order.status !== "CLOSED" ? (
          <Menu>
            <MenuTrigger asChild>
              <Button size="icon-sm" aria-label="More work order actions">
                <MoreHorizontal />
              </Button>
            </MenuTrigger>
            <MenuContent className="w-56">
              <MenuItem
                icon={order.blocks_unit ? <LockOpen /> : <Lock />}
                disabled={update.isPending}
                onSelect={() => update.mutate({ blocks_unit: !order.blocks_unit })}
              >
                {order.blocks_unit ? "Stop blocking the unit" : "Block unit for new bookings"}
              </MenuItem>
            </MenuContent>
          </Menu>
        ) : null}
      </div>
      {prompt ? (
        <div className="space-y-2 rounded-md border bg-surface-raised p-3">
          <label htmlFor="wo-notes" className="text-xs font-medium text-muted-foreground">
            {prompt === "block" ? "Why is the work on hold?" : "Resolution notes (optional)"}
          </label>
          <Textarea id="wo-notes" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} autoFocus />
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="primary"
              disabled={action.isPending || (prompt === "block" && !notes.trim())}
              onClick={() => run(prompt, notes.trim() || undefined)}
            >
              {prompt === "block" ? "Put on hold" : "Mark resolved"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setPrompt(null)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function WorkOrderDetail({ order }: { order: WorkOrder }) {
  const { canManage, canWork } = useWorkOrderPermissions(order);
  const fresh = useRecentlyChanged(order.id);
  const now = useNow();
  const tz = order.property.timezone;
  const impact = order.guest_impact ?? [];
  return (
    <div data-testid="work-order-detail" data-status={order.status} data-unit-blocked={order.unit_blocked ? "true" : "false"}>
      <header className={cn("border-b px-5 pt-5 pb-4", fresh && "animate-row-in")}>
        <div className="pr-8">
          <p className="text-xs text-muted-foreground">
            {MAINTENANCE_CATEGORY_LABEL[order.category]} · open {formatAge(order.created_at, now)}
          </p>
          <h2 className="text-lg leading-tight font-semibold">{order.title}</h2>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <MaintenanceStatusBadge status={order.status} />
            <PriorityLabel priority={order.priority} />
            {order.unit_blocked ? (
              <span className="inline-flex items-center gap-1 rounded-[5px] border border-danger/20 bg-danger/7 px-1.5 text-[11px] leading-[18px] font-medium text-danger" data-testid="unit-blocked-badge">
                <Lock className="size-3" aria-hidden /> Unit blocked
              </span>
            ) : null}
          </div>
        </div>
        {canManage || canWork ? <Actions order={order} canManage={canManage} canWork={canWork} /> : null}
      </header>

      <Section title="Issue">
        <dl>
          <Row label="Property">
            <Link className="text-accent hover:underline" href={`/properties/${order.property.id}`}>
              {order.property.name}
            </Link>
          </Row>
          <Row label="Unit">
            {order.unit.name}
            {order.blocks_unit ? <span className="text-muted-foreground"> · {order.unit_blocked ? "not bookable until resolved" : "was blocked"}</span> : null}
          </Row>
          <Row label="Technician">
            {order.assignee ? (
              <span className="inline-flex items-center gap-1.5" data-testid="technician">
                <Avatar name={order.assignee.name} size="xs" /> {order.assignee.name}
              </span>
            ) : (
              <span className="text-subtle-foreground" data-testid="technician">
                Unassigned
              </span>
            )}
          </Row>
          <Row label="Reported">
            {order.reported_by_label ?? "—"} · {formatFull(order.created_at, tz)}
          </Row>
          {order.resolved_at ? <Row label="Resolved">{formatFull(order.resolved_at, tz)}</Row> : null}
          {order.blocked_reason ? <Row label="On hold">{order.blocked_reason}</Row> : null}
          {order.resolution_notes ? <Row label="Resolution">{order.resolution_notes}</Row> : null}
        </dl>
        {order.description ? <p className="mt-2 rounded-md border bg-surface-raised px-3 py-2 text-[13px] whitespace-pre-wrap">{order.description}</p> : null}
      </Section>

      <Section title="Guest impact">
        {impact.length === 0 ? (
          <p className="text-xs text-subtle-foreground">{order.status === "RESOLVED" || order.status === "CLOSED" ? "Resolved." : "No guests in the unit in the next 14 days."}</p>
        ) : (
          <ul className="divide-y rounded-md border" data-testid="guest-impact">
            {impact.map((g) => (
              <li key={g.reservation_id}>
                <Link href={`/reservations?r=${g.reservation_id}`} className="flex items-center gap-2 px-3 py-2 text-[13px] hover:bg-muted/40">
                  <Users className="size-3.5 text-muted-foreground" aria-hidden />
                  <span className="min-w-0 flex-1 truncate">
                    {g.guest_name} <span className="text-muted-foreground">· {formatStayRange(g.check_in, g.check_out, tz)}</span>
                  </span>
                  <span className={cn("text-xs", g.in_house ? "font-medium text-danger" : "text-muted-foreground")}>{g.in_house ? "In house" : "Upcoming"}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Related">
        <dl>
          <Row label="Reservation">
            {order.reservation ? (
              <Link className="text-accent hover:underline" href={`/reservations?r=${order.reservation.id}`}>
                {order.reservation.guest_name} · <span className="font-mono">{order.reservation.confirmation_code}</span>
              </Link>
            ) : (
              <span className="text-subtle-foreground">None</span>
            )}
          </Row>
          <Row label="Found during">
            {order.source_task ? (
              <Link
                className="inline-flex items-center gap-1 text-accent hover:underline"
                href={order.source_task.cleaning_id ? `/cleaning?c=${order.source_task.cleaning_id}` : `/tasks?t=${order.source_task.id}`}
                data-testid="source-task-link"
              >
                <Sparkles className="size-3.5" aria-hidden /> {order.source_task.title} · {TASK_TYPE_LABEL[order.source_task.task_type]}
              </Link>
            ) : (
              <span className="text-subtle-foreground">Reported directly</span>
            )}
          </Row>
        </dl>
      </Section>

      <Section title="Timeline · audit">
        <EntityTimeline entityIds={[order.id]} tz={tz} />
      </Section>
    </div>
  );
}

function DrawerBody({ id }: { id: string }) {
  const order = useWorkOrder(id);
  if (order.isPending) return <Skeleton className="m-5 h-60" />;
  if (order.error) {
    if (order.error instanceof ApiError && order.error.status === 404) {
      return <EmptyState icon={SearchX} title="Work order not found" />;
    }
    return <ErrorState error={order.error} onRetry={() => order.refetch()} />;
  }
  return <WorkOrderDetail order={order.data} />;
}

export function WorkOrderDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  return (
    <Sheet open={Boolean(id)} onOpenChange={(open) => !open && onClose()} title="Work order" size="lg" hideTitle bodyClassName="p-0">
      {id ? <DrawerBody id={id} /> : null}
    </Sheet>
  );
}
