import type { CleaningStatus, MaintenanceStatus, TaskPriority, TaskStatus, UnitOperationalStatus } from "@stayops/shared";
import { ArrowDown, ArrowUp, ChevronsUp, Minus } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

import {
  CLEANING_STATUS_LABEL,
  formatDue,
  MAINTENANCE_STATUS_LABEL,
  TASK_PRIORITY_LABEL,
  TASK_STATUS_LABEL,
  UNIT_OPS_LABEL,
} from "../format";

const TASK_TONE = {
  TODO: "neutral",
  IN_PROGRESS: "info",
  COMPLETED: "success",
  CANCELLED: "outline",
} as const satisfies Record<TaskStatus, string>;

export function TaskStatusBadge({ status }: { status: TaskStatus }) {
  return (
    <Badge tone={TASK_TONE[status]} dot data-status={status}>
      {TASK_STATUS_LABEL[status]}
    </Badge>
  );
}

const CLEANING_TONE = {
  CREATED: "outline",
  ASSIGNED: "neutral",
  IN_PROGRESS: "info",
  COMPLETED: "accent",
  INSPECTION_REQUIRED: "warning",
  APPROVED: "success",
  FAILED: "danger",
} as const satisfies Record<CleaningStatus, string>;

export function CleaningStatusBadge({ status }: { status: CleaningStatus }) {
  return (
    <Badge tone={CLEANING_TONE[status]} dot data-status={status}>
      {CLEANING_STATUS_LABEL[status]}
    </Badge>
  );
}

const MAINTENANCE_TONE = {
  OPEN: "neutral",
  ASSIGNED: "accent",
  IN_PROGRESS: "info",
  BLOCKED: "warning",
  RESOLVED: "success",
  CLOSED: "outline",
} as const satisfies Record<MaintenanceStatus, string>;

export function MaintenanceStatusBadge({ status }: { status: MaintenanceStatus }) {
  return (
    <Badge tone={MAINTENANCE_TONE[status]} dot data-status={status}>
      {MAINTENANCE_STATUS_LABEL[status]}
    </Badge>
  );
}

const UNIT_TONE = {
  READY: "success",
  OCCUPIED: "info",
  CLEANING: "accent",
  MAINTENANCE: "danger",
  OUT_OF_SERVICE: "outline",
} as const satisfies Record<UnitOperationalStatus, string>;

export function UnitOpsBadge({ status }: { status: UnitOperationalStatus }) {
  return (
    <Badge tone={UNIT_TONE[status]} dot data-testid="unit-ops-status" data-status={status}>
      {UNIT_OPS_LABEL[status]}
    </Badge>
  );
}

const PRIORITY_STYLE: Record<TaskPriority, { icon: typeof ArrowUp; className: string }> = {
  URGENT: { icon: ChevronsUp, className: "text-danger" },
  HIGH: { icon: ArrowUp, className: "text-warning" },
  NORMAL: { icon: Minus, className: "text-subtle-foreground" },
  LOW: { icon: ArrowDown, className: "text-subtle-foreground" },
};

/** Quiet priority marker: only urgent/high draw colour, so boards don't shout. */
export function PriorityLabel({ priority, compact = false }: { priority: TaskPriority; compact?: boolean }) {
  const { icon: Icon, className } = PRIORITY_STYLE[priority];
  return (
    <span
      className={cn("inline-flex items-center gap-1 text-xs font-medium whitespace-nowrap", className)}
      data-priority={priority}
      title={`${TASK_PRIORITY_LABEL[priority]} priority`}
    >
      <Icon className="size-3.5" aria-hidden />
      {compact ? <span className="sr-only">{TASK_PRIORITY_LABEL[priority]}</span> : TASK_PRIORITY_LABEL[priority]}
    </span>
  );
}

export function DueLabel({ dueAt, now, done = false, className }: { dueAt: string | null; now: number; done?: boolean; className?: string }) {
  const due = formatDue(dueAt, now, done);
  return (
    <span
      className={cn(
        "text-xs whitespace-nowrap tabular",
        due.tone === "danger" && "font-medium text-danger",
        due.tone === "warning" && "text-warning",
        due.tone === "muted" && "text-muted-foreground",
        className,
      )}
    >
      {due.label}
    </span>
  );
}
