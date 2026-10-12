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
  "guest.updated",
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
  "conversation.created",
  "conversation.updated",
  "conversation.assigned",
  "message.created",
  "message.read",
  "guest_request.created",
  "guest_request.updated",
  "guest_request.resolved",
  "guest_request.closed",
  "knowledge.created",
  "knowledge.updated",
  "knowledge.archived",
  "guest_portal.link_created",
  "guest_portal.link_revoked",
  "agent.started",
  "agent.tool_called",
  "agent.tool_completed",
  "agent.completed",
  "agent.failed",
  "agent.escalated",
  "ai_action.proposed",
  "ai_action.approved",
  "ai_action.rejected",
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
  "inbox:read",
  "inbox:write",
  "guest_requests:manage",
  "knowledge:write",
  "copilot:use",
  "automations:read",
  "automations:manage",
  "automations:approve",
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

// ------------------------------------------------------------- guest experience (Phase 4)

export const ACTOR_TYPES = ["USER", "SYSTEM", "INTEGRATION", "AI_AGENT", "GUEST"] as const;
export type ActorType = (typeof ACTOR_TYPES)[number];

export const CHANNELS = ["IN_APP", "EMAIL", "SMS", "WHATSAPP", "AIRBNB", "BOOKING", "OTHER"] as const;
export type Channel = (typeof CHANNELS)[number];

export const CONVERSATION_STATUSES = ["OPEN", "PENDING", "RESOLVED", "CLOSED"] as const;
export type ConversationStatus = (typeof CONVERSATION_STATUSES)[number];

export const CONVERSATION_PRIORITIES = ["LOW", "NORMAL", "HIGH", "URGENT"] as const;
export type ConversationPriority = (typeof CONVERSATION_PRIORITIES)[number];

export const INBOX_VIEWS = ["all", "unread", "open", "mine", "high", "resolved"] as const;
export type InboxView = (typeof INBOX_VIEWS)[number];

export const SENDER_TYPES = ["GUEST", "STAFF", "SYSTEM", "AI"] as const;
export type SenderType = (typeof SENDER_TYPES)[number];

export const MESSAGE_TYPES = ["TEXT", "SYSTEM", "REQUEST", "STATUS"] as const;
export type MessageType = (typeof MESSAGE_TYPES)[number];

export const GUEST_REQUEST_TYPES = [
  "EARLY_CHECK_IN",
  "LATE_CHECKOUT",
  "EXTRA_TOWELS",
  "HOUSEKEEPING",
  "MAINTENANCE",
  "QUESTION",
  "OTHER",
] as const;
export type GuestRequestType = (typeof GUEST_REQUEST_TYPES)[number];

export const GUEST_REQUEST_STATUSES = ["OPEN", "ACKNOWLEDGED", "IN_PROGRESS", "RESOLVED", "CLOSED", "CANCELLED"] as const;
export type GuestRequestStatus = (typeof GUEST_REQUEST_STATUSES)[number];

export const GUEST_REQUEST_SOURCES = ["GUEST", "STAFF", "AI", "SYSTEM"] as const;
export type GuestRequestSource = (typeof GUEST_REQUEST_SOURCES)[number];

export const KNOWLEDGE_CATEGORIES = [
  "WIFI",
  "CHECK_IN",
  "CHECK_OUT",
  "HOUSE_RULES",
  "PARKING",
  "DIRECTIONS",
  "AMENITIES",
  "APPLIANCES",
  "EMERGENCY",
  "LOCAL_INFO",
  "OTHER",
] as const;
export type KnowledgeCategory = (typeof KNOWLEDGE_CATEGORIES)[number];

export const KNOWLEDGE_VISIBILITIES = ["PUBLIC", "STAY", "INTERNAL"] as const;
export type KnowledgeVisibility = (typeof KNOWLEDGE_VISIBILITIES)[number];

export const STAY_PHASES = ["UPCOMING", "ARRIVING_TODAY", "IN_HOUSE", "DEPARTING_TODAY", "DEPARTED"] as const;
export type StayPhase = (typeof STAY_PHASES)[number];

// ------------------------------------------------------------- AI (Phase 5)

export const AGENT_TYPES = ["GUEST_AGENT", "OPERATOR_COPILOT"] as const;
export type AgentType = (typeof AGENT_TYPES)[number];

export const RUN_STATUSES = ["RUNNING", "COMPLETED", "AWAITING_APPROVAL", "ESCALATED", "FAILED"] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];

export const STEP_KINDS = ["CONTEXT", "LLM", "TOOL", "PROPOSAL", "GUARDRAIL", "ANSWER"] as const;
export type StepKind = (typeof STEP_KINDS)[number];

export const STEP_STATUSES = ["STARTED", "COMPLETED", "FAILED", "BLOCKED"] as const;
export type StepStatus = (typeof STEP_STATUSES)[number];

export const PROPOSAL_STATUSES = ["PENDING", "APPROVED", "EXECUTED", "REJECTED", "FAILED"] as const;
export type ProposalStatus = (typeof PROPOSAL_STATUSES)[number];
