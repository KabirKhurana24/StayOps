import type { OperationalSignal, SignalSeverity } from "@stayops/shared";

export const SEVERITY_STYLE: Record<SignalSeverity, { dot: string; bar: string; text: string; label: string }> = {
  critical: { dot: "bg-danger", bar: "bg-danger", text: "text-danger", label: "Critical" },
  warning: { dot: "bg-warning", bar: "bg-warning", text: "text-warning", label: "Warning" },
  info: { dot: "bg-info", bar: "bg-info/70", text: "text-info", label: "Info" },
};

/** Short category shown above each signal, and the primary action label. */
export const SIGNAL_META: Record<string, { category: string; action: string }> = {
  late_check_out: { category: "Overstay", action: "View" },
  late_check_in: { category: "Late arrival", action: "Open" },
  pending_arrival: { category: "Unconfirmed", action: "Review" },
  unit_unavailable: { category: "Unit conflict", action: "Resolve" },
  tight_turnover: { category: "Turnover risk", action: "View" },
  same_day_turnover: { category: "Turnover", action: "View" },
  overdue_cleaning: { category: "Overdue cleaning", action: "Open" },
  unassigned_urgent_task: { category: "Unassigned", action: "Assign" },
  failed_inspection: { category: "Inspection failed", action: "Open" },
  high_priority_maintenance: { category: "Maintenance", action: "Open" },
  blocked_unit: { category: "Blocked unit", action: "View" },
};

/** Where a signal's primary action leads: the most specific record it references. */
export function signalHref(signal: OperationalSignal): string | null {
  if (signal.cleaning_id) return `/cleaning?c=${signal.cleaning_id}`;
  if (signal.task_id) return `/tasks?t=${signal.task_id}`;
  if (signal.work_order_id) return `/maintenance?wo=${signal.work_order_id}`;
  if (signal.reservation_id) return `/reservations?r=${signal.reservation_id}`;
  if (signal.property_id) return `/properties/${signal.property_id}`;
  return null;
}

export function signalMeta(signal: OperationalSignal) {
  return SIGNAL_META[signal.code] ?? { category: signal.code.replaceAll("_", " "), action: "View" };
}
