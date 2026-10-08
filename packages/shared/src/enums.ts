// Mirrors the backend enums (apps/api/app/**/models.py). Keep in sync.

export const ROLES = ["OWNER", "ADMIN", "OPERATOR", "STAFF", "VIEWER"] as const;
export type Role = (typeof ROLES)[number];

export const PROPERTY_STATUSES = ["ACTIVE", "INACTIVE", "ARCHIVED"] as const;
export type PropertyStatus = (typeof PROPERTY_STATUSES)[number];

export const UNIT_TYPES = ["APARTMENT", "STUDIO", "ROOM", "HOUSE", "VILLA", "OTHER"] as const;
export type UnitType = (typeof UNIT_TYPES)[number];

export const UNIT_STATUSES = ["ACTIVE", "INACTIVE", "MAINTENANCE"] as const;
export type UnitStatus = (typeof UNIT_STATUSES)[number];

export const RESERVATION_STATUSES = [
  "PENDING",
  "CONFIRMED",
  "CHECKED_IN",
  "CHECKED_OUT",
  "CANCELLED",
] as const;
export type ReservationStatus = (typeof RESERVATION_STATUSES)[number];

export const RESERVATION_SOURCES = [
  "DIRECT",
  "AIRBNB",
  "BOOKING_COM",
  "VRBO",
  "EXPEDIA",
  "OTHER",
] as const;
export type ReservationSource = (typeof RESERVATION_SOURCES)[number];

/** Allowed status transitions — mirrors ALLOWED_TRANSITIONS in reservations/service.py. */
export const RESERVATION_TRANSITIONS: Record<ReservationStatus, readonly ReservationStatus[]> = {
  PENDING: ["CONFIRMED", "CANCELLED"],
  CONFIRMED: ["PENDING", "CHECKED_IN", "CANCELLED"],
  CHECKED_IN: ["CHECKED_OUT"],
  CHECKED_OUT: [],
  CANCELLED: ["CONFIRMED", "PENDING"],
};

export const EVENT_TYPES = [
  "organization.created",
  "property.created",
  "property.updated",
  "unit.created",
  "unit.blocked",
  "unit.unblocked",
  "guest.created",
  "reservation.created",
  "reservation.updated",
  "reservation.cancelled",
  "reservation.checked_in",
  "reservation.checked_out",
  "task.created",
  "task.updated",
  "task.assigned",
  "task.started",
  "task.completed",
  "task.cancelled",
  "task.reopened",
  "cleaning.created",
  "cleaning.assigned",
  "cleaning.started",
  "cleaning.updated",
  "cleaning.completed",
  "cleaning.inspection_required",
  "cleaning.approved",
  "cleaning.failed",
  "maintenance.created",
  "maintenance.updated",
  "maintenance.assigned",
  "maintenance.started",
  "maintenance.blocked",
  "maintenance.resolved",
  "maintenance.closed",
] as const;
export type EventType = (typeof EVENT_TYPES)[number];

export const PERMISSIONS = [
  "org:read",
  "org:manage",
  "properties:read",
  "properties:write",
  "units:write",
  "guests:read",
  "guests:write",
  "reservations:read",
  "reservations:write",
  "activity:read",
  "tasks:read",
  "tasks:manage",
  "tasks:work",
  "maintenance:read",
  "maintenance:manage",
  "maintenance:work",
] as const;
export type Permission = (typeof PERMISSIONS)[number];

export const SIGNAL_SEVERITIES = ["critical", "warning", "info"] as const;
export type SignalSeverity = (typeof SIGNAL_SEVERITIES)[number];

// ------------------------------------------------------------- operations (Phase 3)

export const EVENT_STATUSES = ["PENDING", "PROCESSING", "COMPLETED", "FAILED"] as const;
export type EventStatus = (typeof EVENT_STATUSES)[number];

export const TASK_STATUSES = ["TODO", "IN_PROGRESS", "COMPLETED", "CANCELLED"] as const;
export type TaskStatus = (typeof TASK_STATUSES)[number];

export const TASK_PRIORITIES = ["URGENT", "HIGH", "NORMAL", "LOW"] as const;
export type TaskPriority = (typeof TASK_PRIORITIES)[number];

export const TASK_TYPES = ["TURNOVER_CLEANING", "INSPECTION", "MAINTENANCE", "GUEST_REQUEST", "OTHER"] as const;
export type TaskType = (typeof TASK_TYPES)[number];

/** Mirrors ALLOWED_TRANSITIONS in tasks/service.py (the server enforces it). */
export const TASK_TRANSITIONS: Record<TaskStatus, readonly TaskStatus[]> = {
  TODO: ["IN_PROGRESS", "COMPLETED", "CANCELLED"],
  IN_PROGRESS: ["COMPLETED", "CANCELLED"],
  COMPLETED: [],
  CANCELLED: [],
};

export const TASK_VIEWS = ["all", "mine", "today", "overdue", "completed"] as const;
export type TaskView = (typeof TASK_VIEWS)[number];

export const CLEANING_STATUSES = [
  "CREATED",
  "ASSIGNED",
  "IN_PROGRESS",
  "COMPLETED",
  "INSPECTION_REQUIRED",
  "APPROVED",
  "FAILED",
] as const;
export type CleaningStatus = (typeof CLEANING_STATUSES)[number];

export const CLEANING_VIEWS = ["today", "upcoming", "inspection", "completed"] as const;
export type CleaningView = (typeof CLEANING_VIEWS)[number];

export const MAINTENANCE_CATEGORIES = [
  "HVAC",
  "PLUMBING",
  "ELECTRICAL",
  "APPLIANCE",
  "LOCK",
  "FURNITURE",
  "CLEANING",
  "OTHER",
] as const;
export type MaintenanceCategory = (typeof MAINTENANCE_CATEGORIES)[number];

export const MAINTENANCE_STATUSES = ["OPEN", "ASSIGNED", "IN_PROGRESS", "BLOCKED", "RESOLVED", "CLOSED"] as const;
export type MaintenanceStatus = (typeof MAINTENANCE_STATUSES)[number];

/** Mirrors ALLOWED_TRANSITIONS in maintenance/service.py. */
export const MAINTENANCE_TRANSITIONS: Record<MaintenanceStatus, readonly MaintenanceStatus[]> = {
  OPEN: ["ASSIGNED", "IN_PROGRESS", "RESOLVED", "CLOSED"],
  ASSIGNED: ["OPEN", "IN_PROGRESS", "BLOCKED", "RESOLVED", "CLOSED"],
  IN_PROGRESS: ["BLOCKED", "RESOLVED", "CLOSED"],
  BLOCKED: ["IN_PROGRESS", "RESOLVED", "CLOSED"],
  RESOLVED: ["CLOSED", "IN_PROGRESS"],
  CLOSED: [],
};

export const MAINTENANCE_VIEWS = ["all", "open", "assigned", "in_progress", "blocked", "resolved"] as const;
export type MaintenanceView = (typeof MAINTENANCE_VIEWS)[number];

export const UNIT_OPERATIONAL_STATUSES = ["READY", "OCCUPIED", "CLEANING", "MAINTENANCE", "OUT_OF_SERVICE"] as const;
export type UnitOperationalStatus = (typeof UNIT_OPERATIONAL_STATUSES)[number];
