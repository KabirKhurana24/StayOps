/** Pure Autopilot presentation logic (unit-tested): labels, links, timelines. */
import type {
  AlertSeverity,
  ApprovalStatus,
  ExecutionDetail,
  ExecutionStatus,
  ExecutionStepStatus,
  SimOutcome,
  WorkflowStatus,
} from "@stayops/shared";

export type Tone = "neutral" | "info" | "success" | "warning" | "danger" | "accent" | "outline";

export const WORKFLOW_STATUS: Record<WorkflowStatus, { label: string; tone: Tone }> = {
  DRAFT: { label: "Draft", tone: "neutral" },
  ACTIVE: { label: "Active", tone: "success" },
  PAUSED: { label: "Paused", tone: "warning" },
  ARCHIVED: { label: "Archived", tone: "outline" },
};

export const EXECUTION_STATUS: Record<ExecutionStatus, { label: string; tone: Tone }> = {
  PENDING: { label: "Queued", tone: "neutral" },
  RUNNING: { label: "Running", tone: "info" },
  WAITING_APPROVAL: { label: "Waiting for approval", tone: "warning" },
  COMPLETED: { label: "Completed", tone: "success" },
  FAILED: { label: "Failed", tone: "danger" },
  CANCELLED: { label: "Cancelled", tone: "outline" },
};

export const STEP_STATUS: Record<ExecutionStepStatus, { label: string; tone: Tone }> = {
  PENDING: { label: "Pending retry", tone: "neutral" },
  RUNNING: { label: "Running", tone: "info" },
  COMPLETED: { label: "Done", tone: "success" },
  FAILED: { label: "Failed", tone: "danger" },
  SKIPPED: { label: "Skipped", tone: "outline" },
  WAITING_APPROVAL: { label: "Waiting for approval", tone: "warning" },
  REJECTED: { label: "Rejected", tone: "danger" },
};

export const APPROVAL_STATUS: Record<ApprovalStatus, { label: string; tone: Tone }> = {
  PENDING: { label: "Awaiting approval", tone: "warning" },
  APPROVED: { label: "Approved", tone: "info" },
  EXECUTED: { label: "Approved · done", tone: "success" },
  REJECTED: { label: "Rejected", tone: "outline" },
  FAILED: { label: "Approved · not done", tone: "danger" },
  CANCELLED: { label: "Withdrawn", tone: "outline" },
};

export const ALERT_SEVERITY: Record<AlertSeverity, { label: string; tone: Tone }> = {
  INFO: { label: "Info", tone: "info" },
  WARNING: { label: "Warning", tone: "warning" },
  CRITICAL: { label: "Critical", tone: "danger" },
};

export const SIM_OUTCOME: Record<SimOutcome, { label: string; tone: Tone }> = {
  matched: { label: "Trigger matches", tone: "success" },
  not_matched: { label: "Trigger doesn't match", tone: "outline" },
  passed: { label: "Condition passes", tone: "success" },
  failed: { label: "Condition not met", tone: "outline" },
  would_execute: { label: "Would run", tone: "accent" },
  requires_approval: { label: "Would wait for approval", tone: "warning" },
  invalid: { label: "Would fail", tone: "danger" },
  skipped: { label: "Skipped", tone: "outline" },
  would_stop: { label: "Would stop", tone: "neutral" },
};

/** Where an entity opens in the app (null when it has no screen). */
export function entityHref(type: string | null | undefined, id: string | null | undefined): string | null {
  if (!type || !id) return null;
  switch (type) {
    case "task":
    case "cleaning":
      return type === "task" ? `/tasks?t=${id}` : `/cleaning?c=${id}`;
    case "work_order":
      return `/maintenance?wo=${id}`;
    case "reservation":
      return `/reservations/${id}`;
    case "conversation":
    case "message":
      return type === "conversation" ? `/inbox?c=${id}` : null;
    case "guest_request":
      return null;
    case "property":
      return `/properties/${id}`;
    case "alert":
      return "/automations?tab=alerts";
    default:
      return null;
  }
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} s`;
  const m = Math.floor(s / 60);
  return `${m} min ${Math.round(s % 60)} s`;
}

export function successRate(rate: number | null): string {
  return rate === null ? "—" : `${Math.round(rate * 100)}%`;
}

export type TimelineEntry = {
  key: string;
  at: string | null;
  title: string;
  detail?: string;
  tone: Tone;
  kind: "event" | "trigger" | "condition" | "action" | "end";
  stepId?: string;
};

/**
 * The execution timeline, built only from persisted records: when the run was
 * recorded, each step row (in order), and the final state. Nothing is invented.
 */
export function timeline(detail: ExecutionDetail): TimelineEntry[] {
  const entries: TimelineEntry[] = [
    {
      key: "received",
      at: detail.started_at,
      title: detail.triggering_event_id ? "Event received" : "Scheduled occurrence due",
      detail: String(detail.trigger.type ?? detail.trigger_type),
      tone: "neutral",
      kind: "event",
    },
  ];
  for (const s of detail.steps) {
    if (s.status === "SKIPPED") continue;
    const out = s.output_summary as Record<string, unknown>;
    let title = s.label;
    let info: string | undefined;
    if (s.step_type === "trigger") {
      title = "Workflow matched";
      info = s.label;
    } else if (s.step_type === "condition") {
      title = out.result ? `Condition passed · ${s.label}` : `Condition not met · ${s.label}`;
    } else if (s.status === "COMPLETED") {
      info = typeof out.summary === "string" ? out.summary : undefined;
      if (out.approved_by) info = `${info ?? ""} — approved by ${String(out.approved_by)}`.trim();
    } else if (s.status === "FAILED") {
      info = s.error_message ?? undefined;
    } else if (s.status === "WAITING_APPROVAL") {
      info = typeof out.summary === "string" ? out.summary : undefined;
    } else if (s.status === "REJECTED") {
      info = out.rejected_by ? `Rejected by ${String(out.rejected_by)}` : undefined;
    }
    entries.push({
      key: s.id,
      at: s.completed_at ?? s.started_at,
      title: s.attempt > 1 ? `${title} (attempt ${s.attempt})` : title,
      detail: info,
      tone: s.step_type === "condition" ? (out.result ? "success" : "outline") : STEP_STATUS[s.status].tone,
      kind: s.step_type === "trigger" ? "trigger" : s.step_type === "condition" ? "condition" : "action",
      stepId: s.step_id,
    });
  }
  if (detail.completed_at || ["FAILED", "CANCELLED", "COMPLETED"].includes(detail.status)) {
    const end: Record<string, string> = {
      COMPLETED: "Workflow completed",
      FAILED: "Workflow failed",
      CANCELLED: "Workflow cancelled",
    };
    entries.push({
      key: "end",
      at: detail.completed_at,
      title: end[detail.status] ?? EXECUTION_STATUS[detail.status].label,
      detail: detail.error_summary ?? undefined,
      tone: EXECUTION_STATUS[detail.status].tone,
      kind: "end",
    });
  } else if (detail.status === "WAITING_APPROVAL") {
    entries.push({ key: "waiting", at: null, title: "Paused until someone decides", tone: "warning", kind: "end" });
  }
  return entries;
}

export const skippedSteps = (detail: ExecutionDetail) => detail.steps.filter((s) => s.status === "SKIPPED");
