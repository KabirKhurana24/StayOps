/**
 * Display helpers for operations. Pure functions (no React, no aliases) so they run
 * under `node --test`. Business rules — tight-turnover thresholds, overdue, what a
 * status allows — come from the API; this file only formats.
 */
import type {
  CleaningStatus,
  MaintenanceCategory,
  MaintenanceStatus,
  TaskPriority,
  TaskStatus,
  TaskType,
  UnitOperationalStatus,
} from "@stayops/shared";

/** "4h", "2h 30m", "45m"; negative windows are overlaps. */
export function formatDuration(minutes: number): string {
  if (minutes < 0) return `−${formatDuration(-minutes)}`;
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}

/** "4h turnover" / "No next arrival". */
export function formatTurnover(minutes: number | null): string {
  if (minutes === null) return "No next arrival";
  if (minutes < 0) return "Overlap";
  if (minutes >= 48 * 60) return `${Math.round(minutes / (24 * 60))}d turnover`;
  return `${formatDuration(minutes)} turnover`;
}

/** Relative due label: "Due in 3h", "Overdue 2h 10m", "Due now". */
export function formatDue(dueIso: string | null, now: number, done = false): { label: string; tone: "danger" | "warning" | "muted" } {
  if (!dueIso) return { label: "No due time", tone: "muted" };
  const diff = Math.round((Date.parse(dueIso) - now) / 60_000);
  if (done) return { label: "Done", tone: "muted" };
  if (diff < 0) return { label: `Overdue ${formatDuration(-diff)}`, tone: "danger" };
  if (diff < 1) return { label: "Due now", tone: "warning" };
  if (diff <= 120) return { label: `Due in ${formatDuration(diff)}`, tone: "warning" };
  if (diff < 48 * 60) return { label: `Due in ${formatDuration(diff)}`, tone: "muted" };
  return { label: `Due in ${Math.round(diff / (24 * 60))}d`, tone: "muted" };
}

/** Age of a work order: "3d", "5h", "12m". */
export function formatAge(createdIso: string, now: number): string {
  const minutes = Math.max(0, Math.round((now - Date.parse(createdIso)) / 60_000));
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 48 * 60) return `${Math.floor(minutes / 60)}h`;
  return `${Math.floor(minutes / (24 * 60))}d`;
}

export const TASK_STATUS_LABEL: Record<TaskStatus, string> = {
  TODO: "To do",
  IN_PROGRESS: "In progress",
  COMPLETED: "Completed",
  CANCELLED: "Cancelled",
};

export const TASK_PRIORITY_LABEL: Record<TaskPriority, string> = {
  URGENT: "Urgent",
  HIGH: "High",
  NORMAL: "Normal",
  LOW: "Low",
};

export const TASK_TYPE_LABEL: Record<TaskType, string> = {
  TURNOVER_CLEANING: "Turnover cleaning",
  INSPECTION: "Inspection",
  MAINTENANCE: "Maintenance",
  GUEST_REQUEST: "Guest request",
  OTHER: "Other",
};

export const CLEANING_STATUS_LABEL: Record<CleaningStatus, string> = {
  CREATED: "Unassigned",
  ASSIGNED: "Assigned",
  IN_PROGRESS: "Cleaning",
  COMPLETED: "Cleaned",
  INSPECTION_REQUIRED: "Awaiting inspection",
  APPROVED: "Ready",
  FAILED: "Re-clean",
};

export const MAINTENANCE_STATUS_LABEL: Record<MaintenanceStatus, string> = {
  OPEN: "Open",
  ASSIGNED: "Assigned",
  IN_PROGRESS: "In progress",
  BLOCKED: "Blocked",
  RESOLVED: "Resolved",
  CLOSED: "Closed",
};

export const MAINTENANCE_CATEGORY_LABEL: Record<MaintenanceCategory, string> = {
  HVAC: "HVAC",
  PLUMBING: "Plumbing",
  ELECTRICAL: "Electrical",
  APPLIANCE: "Appliance",
  LOCK: "Lock",
  FURNITURE: "Furniture",
  CLEANING: "Cleaning",
  OTHER: "Other",
};

export const UNIT_OPS_LABEL: Record<UnitOperationalStatus, string> = {
  READY: "Ready",
  OCCUPIED: "Occupied",
  CLEANING: "Turnover",
  MAINTENANCE: "Blocked",
  OUT_OF_SERVICE: "Out of service",
};

const PRIORITY_ORDER: Record<TaskPriority, number> = { URGENT: 0, HIGH: 1, NORMAL: 2, LOW: 3 };

/** Client-side tie-break identical to the API's default board order. */
export function compareByPriorityThenDue(
  a: { priority: TaskPriority; due_at: string | null },
  b: { priority: TaskPriority; due_at: string | null },
): number {
  const p = PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority];
  if (p !== 0) return p;
  if (a.due_at === b.due_at) return 0;
  if (a.due_at === null) return 1;
  if (b.due_at === null) return -1;
  return Date.parse(a.due_at) - Date.parse(b.due_at);
}
