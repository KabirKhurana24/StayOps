"use client";

import {
  type ConversationContext,
  GUEST_REQUEST_LABEL,
  type GuestRequest,
  MAINTENANCE_CATEGORIES,
  type MaintenanceCategory,
  TASK_PRIORITIES,
  type TaskPriority,
} from "@stayops/shared";
import {
  BedDouble,
  Building2,
  Check,
  ClipboardList,
  Copy,
  ExternalLink,
  Link2,
  ListChecks,
  Lock,
  Mail,
  Phone,
  UserRound,
  Wrench,
} from "lucide-react";
import Link from "next/link";
import { type FormEvent, type ReactNode, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { Field, FormError, Input, Select } from "@/components/ui/form";
import { Avatar } from "@/components/ui/misc";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { useRequestAction } from "@/features/guest-requests/api";
import { useCreateWorkOrder } from "@/features/maintenance/api";
import { useCreateTask } from "@/features/tasks/api";
import { ApiError, newIdempotencyKey } from "@/lib/api/client";
import { formatInZone, formatRelative, formatStayRange, formatTime } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useConversationContext, useIssuePortalLink, useRevokePortalLink } from "../api";
import { PHASE_LABEL, portalUrl, REQUEST_STATUS_LABEL, REQUEST_STATUS_TONE, requestActions } from "../model";
import { ConvertToMaintenanceDialog, ConvertToTaskDialog } from "./request-dialogs";

function Section({ title, icon, children, actions }: { title: string; icon: ReactNode; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="border-b px-4 py-3.5 last:border-b-0">
      <header className="mb-2 flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-[11px] font-semibold tracking-wide text-muted-foreground uppercase [&_svg]:size-3.5">
          {icon}
          {title}
        </h3>
        {actions}
      </header>
      {children}
    </section>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5 text-[13px]">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 text-right">{children}</span>
    </div>
  );
}

const PREFERENCE_LABEL: Record<string, string> = {
  preferred_language: "Language",
  contact_channel: "Prefers",
  usually_requests_early_check_in: "Often asks for early check-in",
  usually_requests_late_checkout: "Often asks for late checkout",
  bedding: "Bedding",
  accessibility: "Accessibility",
  dietary: "Dietary",
  occasions: "Occasion",
  other: "Note",
};

function RequestCard({ r, tz, canManage }: { r: GuestRequest; tz: string; canManage: boolean }) {
  const action = useRequestAction(r.id);
  const can = requestActions(r);
  const [dialog, setDialog] = useState<"task" | "maintenance" | null>(null);
  return (
    <li className="rounded-md border bg-surface p-2.5" data-testid="guest-request" data-status={r.status}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[13px] font-medium">{GUEST_REQUEST_LABEL[r.type]}</p>
          <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{r.description}</p>
        </div>
        <Badge tone={REQUEST_STATUS_TONE[r.status]} dot>
          {REQUEST_STATUS_LABEL[r.status]}
        </Badge>
      </div>
      {r.requested_for ? <p className="mt-1 text-xs text-muted-foreground">For {formatInZone(r.requested_for, tz, { weekday: "short", hour: "2-digit", minute: "2-digit" })}</p> : null}
      {r.task || r.work_order ? (
        <p className="mt-1.5 flex flex-wrap gap-x-3 text-xs">
          {r.task ? (
            <Link href={`/tasks?t=${r.task.id}`} className="inline-flex items-center gap-1 text-accent hover:underline" data-testid="request-task-link">
              <ListChecks className="size-3" /> {r.task.title} · {r.task.status.replace("_", " ").toLowerCase()}
            </Link>
          ) : null}
          {r.work_order ? (
            <Link href={`/maintenance?wo=${r.work_order.id}`} className="inline-flex items-center gap-1 text-accent hover:underline">
              <Wrench className="size-3" /> {r.work_order.title} · {r.work_order.status.replace("_", " ").toLowerCase()}
            </Link>
          ) : null}
        </p>
      ) : null}
      {canManage && Object.values(can).some(Boolean) ? (
        <div className="mt-2 flex flex-wrap gap-1">
          {can.acknowledge ? (
            <Button size="xs" disabled={action.isPending} onClick={() => action.mutate({ action: "acknowledge" })}>
              <Check /> Acknowledge
            </Button>
          ) : null}
          {can.toTask ? (
            <Button size="xs" onClick={() => setDialog("task")}>
              <ListChecks /> Create task
            </Button>
          ) : null}
          {can.toMaintenance ? (
            <Button size="xs" onClick={() => setDialog("maintenance")}>
              <Wrench /> Maintenance
            </Button>
          ) : null}
          {can.resolve ? (
            <Button size="xs" variant="ghost" disabled={action.isPending} onClick={() => action.mutate({ action: "resolve" })}>
              Resolve
            </Button>
          ) : null}
          {can.close ? (
            <Button size="xs" variant="ghost" disabled={action.isPending} onClick={() => action.mutate({ action: "close" })}>
              Close
            </Button>
          ) : null}
        </div>
      ) : null}
      {dialog === "task" ? <ConvertToTaskDialog request={r} open onOpenChange={(o) => setDialog(o ? "task" : null)} /> : null}
      {dialog === "maintenance" ? <ConvertToMaintenanceDialog request={r} open onOpenChange={(o) => setDialog(o ? "maintenance" : null)} /> : null}
    </li>
  );
}

function PortalLinkCard({ reservationId, status, canWrite }: { reservationId: string; status: ConversationContext["portal_link"]; canWrite: boolean }) {
  const issue = useIssuePortalLink(reservationId);
  const revoke = useRevokePortalLink(reservationId);
  const [url, setUrl] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const create = () =>
    issue.mutate(undefined, {
      onSuccess: (issued) => {
        setUrl(portalUrl(window.location.origin, issued.path));
        setConfirm(false);
      },
    });
  const copy = async () => {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      toast.success("Portal link copied");
    } catch {
      inputRef.current?.select();
    }
  };

  return (
    <div data-testid="portal-link">
      {url ? (
        <div className="space-y-1.5">
          <div className="flex gap-1.5">
            <Input ref={inputRef} readOnly value={url} aria-label="Guest portal link" className="h-7 font-mono text-[11px]" onFocus={(e) => e.currentTarget.select()} />
            <Button size="sm" onClick={copy} aria-label="Copy link">
              <Copy />
            </Button>
          </div>
          <p className="text-[11px] text-muted-foreground">Share it with the guest — it&apos;s shown only once. Creating a new link disables this one.</p>
        </div>
      ) : status?.active ? (
        <p className="text-xs text-muted-foreground">
          Active link · expires {status.expires_at ? formatRelative(status.expires_at) : "after the stay"}
          {status.last_used_at ? ` · opened ${formatRelative(status.last_used_at)}` : " · not opened yet"}
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">No active link. The guest portal shows the stay, house guide, messages and requests.</p>
      )}
      {canWrite ? (
        <div className="mt-2 flex gap-1.5">
          <Button size="xs" disabled={issue.isPending} onClick={() => (status?.active && !url ? setConfirm(true) : create())}>
            <Link2 /> {status?.active || url ? "New link" : "Create link"}
          </Button>
          {status?.active || url ? (
            <Button
              size="xs"
              variant="ghost"
              disabled={revoke.isPending}
              onClick={() => revoke.mutate(undefined, { onSuccess: () => setUrl(null) })}
            >
              Revoke
            </Button>
          ) : null}
        </div>
      ) : null}
      {issue.error ? <p className="mt-1 text-xs text-danger">{issue.error instanceof ApiError ? issue.error.message : "Couldn't create the link."}</p> : null}
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="Create a new portal link?"
        description="The guest's current link will stop working."
        confirmLabel="Create new link"
        onConfirm={create}
      />
    </div>
  );
}

function QuickTaskDialog({ open, onOpenChange, ctx }: { open: boolean; onOpenChange: (o: boolean) => void; ctx: ConversationContext }) {
  const create = useCreateTask();
  const keyRef = useRef(newIdempotencyKey("task"));
  const [title, setTitle] = useState("");
  const [priority, setPriority] = useState<TaskPriority>("NORMAL");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate(
      {
        idempotencyKey: keyRef.current,
        body: { title: title.trim(), task_type: "GUEST_REQUEST", priority, reservation_id: ctx.reservation?.id, unit_id: ctx.unit?.id },
      },
      { onSuccess: () => (toast.success("Task created"), onOpenChange(false)) },
    );
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Create task" description={`For ${ctx.guest.first_name}'s stay · Unit ${ctx.unit?.name ?? ""}`}>
      <form onSubmit={submit} className="space-y-4 px-5 py-4">
        <Field label="Task" htmlFor="quick-task-title">
          <Input id="quick-task-title" required maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Restock coffee pods" />
        </Field>
        <Field label="Priority" htmlFor="quick-task-priority">
          <Select id="quick-task-priority" value={priority} onChange={(e) => setPriority(e.target.value as TaskPriority)}>
            {TASK_PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {p.charAt(0) + p.slice(1).toLowerCase()}
              </option>
            ))}
          </Select>
        </Field>
        <FormError message={create.error instanceof ApiError ? create.error.message : create.error ? "Couldn't create the task." : null} />
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={create.isPending || !title.trim()}>
            Create task
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function QuickMaintenanceDialog({ open, onOpenChange, ctx }: { open: boolean; onOpenChange: (o: boolean) => void; ctx: ConversationContext }) {
  const create = useCreateWorkOrder();
  const keyRef = useRef(newIdempotencyKey("wo"));
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState<MaintenanceCategory>("OTHER");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate(
      {
        idempotencyKey: keyRef.current,
        body: { title: title.trim(), category, priority: "HIGH", unit_id: ctx.unit?.id, reservation_id: ctx.reservation?.id, blocks_unit: false },
      },
      { onSuccess: () => (toast.success("Work order created"), onOpenChange(false)) },
    );
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Create maintenance work order" description={`Unit ${ctx.unit?.name ?? ""} · ${ctx.property?.name ?? ""}`}>
      <form onSubmit={submit} className="space-y-4 px-5 py-4">
        <Field label="Issue" htmlFor="quick-wo-title">
          <Input id="quick-wo-title" required maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Shower drains slowly" />
        </Field>
        <Field label="Category" htmlFor="quick-wo-category">
          <Select id="quick-wo-category" value={category} onChange={(e) => setCategory(e.target.value as MaintenanceCategory)}>
            {MAINTENANCE_CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c.charAt(0) + c.slice(1).toLowerCase()}
              </option>
            ))}
          </Select>
        </Field>
        <FormError message={create.error instanceof ApiError ? create.error.message : create.error ? "Couldn't create the work order." : null} />
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={create.isPending || !title.trim()}>
            Create work order
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

export function ContextPanel({
  conversationId,
  canWrite,
  canManageRequests,
  canManageWork,
}: {
  conversationId: string;
  canWrite: boolean;
  canManageRequests: boolean;
  canManageWork: boolean;
}) {
  const query = useConversationContext(conversationId);
  const [quick, setQuick] = useState<"task" | "maintenance" | null>(null);

  if (query.isPending) {
    return (
      <div className="space-y-3 p-4" aria-busy="true" aria-label="Loading details">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }
  if (query.error) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;

  const ctx = query.data;
  const g = ctx.guest;
  const r = ctx.reservation;
  const p = ctx.property;
  const tz = p?.timezone ?? "UTC";
  const name = `${g.first_name} ${g.last_name}`;
  const prefs = Object.entries(g.preferences).filter(([, v]) => v !== false && v !== null && v !== "");
  const activeRequests = ctx.requests.filter((x) => x.status !== "CLOSED" && x.status !== "CANCELLED");

  return (
    <div className="text-[13px]" data-testid="context-panel">
      <Section
        title="Guest"
        icon={<UserRound />}
        actions={
          <Link href={`/guests/${g.id}`} className="inline-flex items-center gap-1 text-xs text-accent hover:underline">
            View guest <ExternalLink className="size-3" />
          </Link>
        }
      >
        <div className="flex items-center gap-2.5">
          <Avatar name={name} size="md" />
          <div className="min-w-0">
            <p className="truncate font-medium">{name}</p>
            <p className="text-xs text-muted-foreground">
              {g.stats?.total_stays ? `${g.stats.total_stays} stay${g.stats.total_stays === 1 ? "" : "s"}` : "First stay"}
              {g.stats && g.stats.total_stays > 1 ? " · returning guest" : ""}
            </p>
          </div>
        </div>
        <div className="mt-2 space-y-0.5 text-xs">
          {g.email ? (
            <a href={`mailto:${g.email}`} className="flex items-center gap-1.5 text-muted-foreground hover:text-foreground">
              <Mail className="size-3" /> {g.email}
            </a>
          ) : null}
          {g.phone ? (
            <a href={`tel:${g.phone}`} className="flex items-center gap-1.5 text-muted-foreground tabular hover:text-foreground">
              <Phone className="size-3" /> {g.phone}
            </a>
          ) : null}
        </div>
        {prefs.length ? (
          <ul className="mt-2 flex flex-wrap gap-1" aria-label="Preferences">
            {prefs.map(([k, v]) => (
              <li key={k}>
                <Badge tone="outline">{typeof v === "boolean" ? PREFERENCE_LABEL[k] : `${PREFERENCE_LABEL[k] ?? k}: ${String(v)}`}</Badge>
              </li>
            ))}
          </ul>
        ) : null}
        {g.notes ? (
          <p className="mt-2 flex gap-1.5 rounded-md bg-warning/8 px-2 py-1.5 text-xs">
            <Lock className="mt-0.5 size-3 shrink-0 text-warning" aria-label="Team only" />
            <span className="line-clamp-3">{g.notes}</span>
          </p>
        ) : null}
      </Section>

      {r && p ? (
        <Section
          title="Stay"
          icon={<BedDouble />}
          actions={
            <Link href={`/reservations/${r.id}`} className="inline-flex items-center gap-1 text-xs text-accent hover:underline">
              View reservation <ExternalLink className="size-3" />
            </Link>
          }
        >
          <div className="mb-1.5 flex items-center gap-2">
            <span className="font-medium tabular">{r.confirmation_code}</span>
            <Badge tone={r.phase === "IN_HOUSE" || r.phase === "DEPARTING_TODAY" ? "success" : r.phase === "ARRIVING_TODAY" ? "accent" : "neutral"} dot>
              {PHASE_LABEL[r.phase]}
            </Badge>
          </div>
          <Row label="Dates">{formatStayRange(r.check_in, r.check_out, tz)}</Row>
          <Row label="Check-in">{formatInZone(r.check_in, tz, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</Row>
          <Row label="Check-out">{formatInZone(r.check_out, tz, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</Row>
          <Row label="Guests">
            {r.adults} adult{r.adults === 1 ? "" : "s"}
            {r.children ? ` · ${r.children} child${r.children === 1 ? "" : "ren"}` : ""}
          </Row>
          <Row label="Status">{r.status.replace("_", " ").toLowerCase()}</Row>
          {r.notes ? (
            <p className="mt-1.5 flex gap-1.5 rounded-md bg-muted px-2 py-1.5 text-xs">
              <Lock className="mt-0.5 size-3 shrink-0" aria-label="Team only" />
              <span className="line-clamp-3">{r.notes}</span>
            </p>
          ) : null}
        </Section>
      ) : null}

      {p ? (
        <Section
          title="Property"
          icon={<Building2 />}
          actions={
            <Link href={`/properties/${p.id}`} className="inline-flex items-center gap-1 text-xs text-accent hover:underline">
              View property <ExternalLink className="size-3" />
            </Link>
          }
        >
          <p className="font-medium">{p.name}</p>
          <p className="text-xs text-muted-foreground">{p.address}</p>
          {ctx.unit ? (
            <Row label={`Unit ${ctx.unit.name} right now`}>
              <span className={cn("text-xs", ctx.unit.maintenance_blocked ? "text-danger" : "text-muted-foreground")}>
                {ctx.unit.operational_status.replace("_", " ").toLowerCase()}
                {ctx.unit.maintenance_blocked ? " · blocked" : ""}
              </span>
            </Row>
          ) : null}
          <Row label="House times">
            <span className="text-xs tabular">
              in {formatTime(`2000-01-01T${p.check_in_time}Z`, "UTC")} · out {formatTime(`2000-01-01T${p.check_out_time}Z`, "UTC")}
            </span>
          </Row>
        </Section>
      ) : null}

      <Section title="Requests" icon={<ClipboardList />}>
        {activeRequests.length ? (
          <ul className="space-y-2" aria-label="Guest requests">
            {activeRequests.map((x) => (
              <RequestCard key={x.id} r={x} tz={tz} canManage={canManageRequests} />
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">No open requests.</p>
        )}
      </Section>

      <Section title="Operations" icon={<ListChecks />}>
        {ctx.work.length ? (
          <ul className="space-y-1.5" aria-label="Open work for this stay">
            {ctx.work.map((w) => (
              <li key={`${w.kind}-${w.id}`} className="flex items-start justify-between gap-2 text-xs">
                <Link href={w.kind === "task" ? `/tasks?t=${w.id}` : `/maintenance?wo=${w.id}`} className="min-w-0 hover:underline">
                  {w.kind === "task" ? <ListChecks className="mr-1 inline size-3" /> : <Wrench className="mr-1 inline size-3" />}
                  {w.title}
                </Link>
                <span className="shrink-0 text-muted-foreground">
                  {w.status.replace("_", " ").toLowerCase()}
                  {w.assignee ? ` · ${w.assignee}` : ""}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">Nothing open for this stay or unit.</p>
        )}
        {canManageWork && r ? (
          <div className="mt-2 flex gap-1.5">
            <Button size="xs" onClick={() => setQuick("task")}>
              <ListChecks /> Create task
            </Button>
            <Button size="xs" onClick={() => setQuick("maintenance")}>
              <Wrench /> Create maintenance
            </Button>
          </div>
        ) : null}
      </Section>

      {r ? (
        <Section title="Guest portal" icon={<Link2 />}>
          <PortalLinkCard reservationId={r.id} status={ctx.portal_link} canWrite={canWrite} />
        </Section>
      ) : null}

      {quick === "task" ? <QuickTaskDialog open onOpenChange={(o) => setQuick(o ? "task" : null)} ctx={ctx} /> : null}
      {quick === "maintenance" ? <QuickMaintenanceDialog open onOpenChange={(o) => setQuick(o ? "maintenance" : null)} ctx={ctx} /> : null}
    </div>
  );
}
