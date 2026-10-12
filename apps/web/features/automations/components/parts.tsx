"use client";

import type { AlertSeverity, ApprovalStatus, AutomationCatalog, ExecutionStatus, WorkflowStatus } from "@stayops/shared";
import { CalendarClock, Zap } from "lucide-react";

import { Badge } from "@/components/ui/badge";

import { ALERT_SEVERITY, APPROVAL_STATUS, EXECUTION_STATUS, WORKFLOW_STATUS } from "../model";

export function WorkflowStatusBadge({ status }: { status: WorkflowStatus }) {
  const s = WORKFLOW_STATUS[status];
  return (
    <Badge tone={s.tone} dot data-testid="workflow-status" data-status={status}>
      {s.label}
    </Badge>
  );
}

export function ExecutionStatusBadge({ status }: { status: ExecutionStatus }) {
  const s = EXECUTION_STATUS[status];
  return (
    <Badge tone={s.tone} dot data-testid="execution-status" data-status={status}>
      {s.label}
    </Badge>
  );
}

export function ApprovalStatusBadge({ status }: { status: ApprovalStatus }) {
  const s = APPROVAL_STATUS[status];
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

export function SeverityBadge({ severity }: { severity: AlertSeverity }) {
  const s = ALERT_SEVERITY[severity];
  return (
    <Badge tone={s.tone} dot>
      {s.label}
    </Badge>
  );
}

export function TriggerLabel({ type, catalog }: { type: string; catalog: AutomationCatalog | undefined }) {
  const trigger = catalog?.triggers.find((t) => t.type === type);
  const Icon = trigger?.kind === "schedule" ? CalendarClock : Zap;
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5 text-[13px]">
      <Icon className="size-3.5 shrink-0 text-subtle-foreground" aria-hidden />
      <span className="truncate">{trigger?.label ?? type}</span>
    </span>
  );
}

export function RiskBadge({ risk }: { risk: string }) {
  if (risk === "MODERATE") return <Badge tone="warning">Moderate risk</Badge>;
  if (risk === "SYSTEM") return <Badge tone="neutral">Gate</Badge>;
  return <Badge tone="outline">Low risk</Badge>;
}
