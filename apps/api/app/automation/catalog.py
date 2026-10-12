"""The allowlist: every trigger, condition field and operator a workflow may use.

This module is the single source of truth for what a workflow can express. The
validator, the simulator, the visual editor (``GET /automations/catalog``) and
AI-assisted drafting all read it; anything not listed here is rejected.

Fields are *typed facts* about the trigger's subject, computed by
``app.automation.context`` from the database (never taken from event payloads).
Each trigger exposes a set of *scopes*; a field or action is only usable when its
scope is available to the workflow's trigger.
"""

from __future__ import annotations

import builtins
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.alerts.models import AlertSeverity
from app.cleaning.models import CleaningStatus
from app.guest_requests.models import GuestRequestSource, GuestRequestStatus, GuestRequestType
from app.inbox.models import Channel, ConversationStatus, MessageType, SenderType
from app.maintenance.models import MaintenanceCategory, MaintenanceStatus
from app.reservations.models import ReservationSource, ReservationStatus
from app.tasks.models import TaskPriority, TaskStatus, TaskType

FieldType = Literal["enum", "string", "number", "boolean", "id"]


class Op(StrEnum):
    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"


OPS_BY_TYPE: dict[str, tuple[Op, ...]] = {
    "enum": (Op.EQ, Op.NEQ, Op.IN, Op.NOT_IN, Op.EXISTS, Op.NOT_EXISTS),
    "string": (Op.EQ, Op.NEQ, Op.IN, Op.NOT_IN, Op.EXISTS, Op.NOT_EXISTS),
    "id": (Op.EQ, Op.NEQ, Op.IN, Op.NOT_IN, Op.EXISTS, Op.NOT_EXISTS),
    "number": (Op.EQ, Op.NEQ, Op.GT, Op.GTE, Op.LT, Op.LTE, Op.EXISTS, Op.NOT_EXISTS),
    "boolean": (Op.EQ, Op.NEQ, Op.EXISTS, Op.NOT_EXISTS),
}
OP_LABELS = {
    Op.EQ: "is",
    Op.NEQ: "is not",
    Op.GT: "is greater than",
    Op.GTE: "is at least",
    Op.LT: "is less than",
    Op.LTE: "is at most",
    Op.IN: "is one of",
    Op.NOT_IN: "is none of",
    Op.EXISTS: "is set",
    Op.NOT_EXISTS: "is not set",
}


@dataclass(frozen=True, slots=True)
class FieldDef:
    path: str
    label: str
    type: FieldType
    scope: str
    values: tuple[str, ...] = ()
    unit: str | None = None  # "minutes", "hours" … (display)

    @property
    def ops(self) -> tuple[Op, ...]:
        return OPS_BY_TYPE[self.type]


def _values(enum: type[StrEnum]) -> tuple[str, ...]:
    return tuple(e.value for e in enum)


ACTOR_TYPES = ("USER", "SYSTEM", "INTEGRATION", "AI_AGENT", "GUEST")

FIELDS: tuple[FieldDef, ...] = (
    # event (event triggers only)
    FieldDef("event.actor_type", "Changed by", "enum", "event", ACTOR_TYPES),
    # property / unit
    FieldDef("property.id", "Property", "id", "property"),
    FieldDef("property.name", "Property name", "string", "property"),
    FieldDef("unit.id", "Unit", "id", "unit"),
    FieldDef("unit.name", "Unit name", "string", "unit"),
    # reservation
    FieldDef(
        "reservation.status",
        "Reservation status",
        "enum",
        "reservation",
        _values(ReservationStatus),
    ),
    FieldDef(
        "reservation.source", "Booking source", "enum", "reservation", _values(ReservationSource)
    ),
    FieldDef("reservation.confirmation_code", "Confirmation code", "string", "reservation"),
    FieldDef("reservation.nights", "Nights", "number", "reservation", unit="nights"),
    FieldDef("reservation.guests", "Guests (adults + children)", "number", "reservation"),
    FieldDef(
        "reservation.hours_until_check_in",
        "Hours until check-in",
        "number",
        "reservation",
        unit="hours",
    ),
    FieldDef(
        "reservation.dates_changed", "Check-in or check-out changed", "boolean", "reservation"
    ),
    FieldDef("reservation.unit_changed", "Moved to another unit", "boolean", "reservation"),
    # turnover around the reservation (same unit)
    FieldDef(
        "turnover.has_next_arrival", "Another guest arrives after check-out", "boolean", "turnover"
    ),
    FieldDef(
        "turnover.after_checkout_minutes",
        "Turnover window after check-out",
        "number",
        "turnover",
        unit="minutes",
    ),
    FieldDef(
        "turnover.same_day_after_checkout",
        "Next check-in is the same local day",
        "boolean",
        "turnover",
    ),
    FieldDef(
        "turnover.before_check_in_minutes",
        "Turnover window before check-in",
        "number",
        "turnover",
        unit="minutes",
    ),
    FieldDef(
        "turnover.same_day_before_check_in",
        "Previous check-out is the same local day",
        "boolean",
        "turnover",
    ),
    # task
    FieldDef("task.id", "Task", "id", "task"),
    FieldDef("task.title", "Task title", "string", "task"),
    FieldDef("task.task_type", "Task type", "enum", "task", _values(TaskType)),
    FieldDef("task.priority", "Task priority", "enum", "task", _values(TaskPriority)),
    FieldDef("task.status", "Task status", "enum", "task", _values(TaskStatus)),
    FieldDef("task.assigned", "Task is assigned", "boolean", "task"),
    FieldDef("task.assigned_to", "Task assignee", "id", "task"),
    FieldDef("task.overdue_minutes", "Minutes overdue", "number", "task", unit="minutes"),
    FieldDef("task.due_in_minutes", "Minutes until due", "number", "task", unit="minutes"),
    FieldDef("task.age_minutes", "Minutes since created", "number", "task", unit="minutes"),
    # cleaning
    FieldDef("cleaning.status", "Cleaning status", "enum", "cleaning", _values(CleaningStatus)),
    FieldDef("cleaning.failure_count", "Failed inspections", "number", "cleaning"),
    # maintenance
    FieldDef("work_order.title", "Work order title", "string", "work_order"),
    FieldDef(
        "work_order.priority", "Work order priority", "enum", "work_order", _values(TaskPriority)
    ),
    FieldDef(
        "work_order.category",
        "Work order category",
        "enum",
        "work_order",
        _values(MaintenanceCategory),
    ),
    FieldDef(
        "work_order.status", "Work order status", "enum", "work_order", _values(MaintenanceStatus)
    ),
    FieldDef("work_order.blocks_unit", "Blocks the unit", "boolean", "work_order"),
    FieldDef("work_order.assigned", "Work order is assigned", "boolean", "work_order"),
    FieldDef("work_order.age_hours", "Hours since reported", "number", "work_order", unit="hours"),
    # guest requests
    FieldDef(
        "guest_request.type", "Request type", "enum", "guest_request", _values(GuestRequestType)
    ),
    FieldDef(
        "guest_request.priority", "Request priority", "enum", "guest_request", _values(TaskPriority)
    ),
    FieldDef(
        "guest_request.status",
        "Request status",
        "enum",
        "guest_request",
        _values(GuestRequestStatus),
    ),
    FieldDef(
        "guest_request.source", "Raised by", "enum", "guest_request", _values(GuestRequestSource)
    ),
    FieldDef(
        "guest_request.has_work",
        "Already linked to a task or work order",
        "boolean",
        "guest_request",
    ),
    # inbox
    FieldDef(
        "conversation.status",
        "Conversation status",
        "enum",
        "conversation",
        _values(ConversationStatus),
    ),
    FieldDef("conversation.channel", "Channel", "enum", "conversation", _values(Channel)),
    FieldDef("conversation.assigned", "Conversation is assigned", "boolean", "conversation"),
    FieldDef("conversation.has_stay", "Linked to a stay", "boolean", "conversation"),
    FieldDef("message.sender_type", "Sent by", "enum", "message", _values(SenderType)),
    FieldDef("message.message_type", "Message type", "enum", "message", _values(MessageType)),
    FieldDef("message.internal", "Internal note", "boolean", "message"),
    # schedule: upcoming arrival for a cleaning
    FieldDef(
        "arrival.minutes_until_check_in",
        "Minutes until next check-in",
        "number",
        "arrival",
        unit="minutes",
    ),
    FieldDef("arrival.confirmation_code", "Next arrival", "string", "arrival"),
    # schedule: morning summary (per property)
    FieldDef("summary.arrivals_today", "Arrivals today", "number", "summary"),
    FieldDef("summary.departures_today", "Departures today", "number", "summary"),
    FieldDef("summary.open_tasks", "Open tasks", "number", "summary"),
    FieldDef("summary.overdue_tasks", "Overdue tasks", "number", "summary"),
    FieldDef("summary.unassigned_tasks", "Unassigned tasks", "number", "summary"),
    FieldDef("summary.active_maintenance", "Active work orders", "number", "summary"),
    FieldDef("summary.local_date", "Local date", "string", "summary"),
)
FIELDS_BY_PATH: dict[str, FieldDef] = {f.path: f for f in FIELDS}


# ------------------------------------------------------------------ triggers


class TriggerConfig(BaseModel):
    """Filters every trigger accepts. Empty = all properties."""

    model_config = ConfigDict(extra="forbid")
    property_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


class TaskOverdueConfig(TriggerConfig):
    minutes_overdue: int = Field(default=0, ge=0, le=10080)
    task_types: list[TaskType] = Field(default_factory=list, max_length=5)


class CleaningNotReadyConfig(TriggerConfig):
    minutes_before_check_in: int = Field(default=60, ge=15, le=1440)


class MaintenanceOverdueConfig(TriggerConfig):
    hours_open: int = Field(default=4, ge=1, le=336)
    priorities: list[TaskPriority] = Field(
        default_factory=lambda: [TaskPriority.URGENT], min_length=1, max_length=4
    )


class TaskUnassignedConfig(TriggerConfig):
    minutes_unassigned: int = Field(default=60, ge=15, le=10080)
    priorities: list[TaskPriority] = Field(default_factory=list, max_length=4)


class DailySummaryConfig(TriggerConfig):
    local_time: str = Field(default="08:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True, slots=True)
class TriggerDef:
    type: str
    label: str
    category: str
    kind: Literal["event", "schedule"]
    subject: str  # entity type of the subject: reservation, task, …
    scopes: frozenset[str]
    description: str
    config_model: builtins.type[TriggerConfig] = TriggerConfig
    # Entity references actions may target (resolved per execution).
    refs: frozenset[str] = field(default_factory=frozenset)


def _event(
    type_: str, label: str, category: str, subject: str, scopes: set[str], refs: set[str], desc: str
) -> TriggerDef:
    return TriggerDef(
        type=type_,
        label=label,
        category=category,
        kind="event",
        subject=subject,
        scopes=frozenset(scopes | {"event"}),
        description=desc,
        refs=frozenset(refs),
    )


_RES_SCOPES = {"reservation", "property", "unit", "turnover"}
_RES_REFS = {"reservation", "property", "unit"}
_TASK_SCOPES = {"task", "property", "unit"}
_TASK_REFS = {"task", "property", "unit", "reservation"}
_CLEAN_SCOPES = {"cleaning", "task", "property", "unit"}
_WO_SCOPES = {"work_order", "property", "unit"}
_WO_REFS = {"work_order", "property", "unit", "reservation"}
_GR_SCOPES = {"guest_request", "property", "unit"}
_GR_REFS = {"guest_request", "conversation", "reservation", "property", "unit"}

TRIGGERS: tuple[TriggerDef, ...] = (
    _event(
        "reservation.created",
        "Reservation created",
        "Reservations",
        "reservation",
        _RES_SCOPES,
        _RES_REFS,
        "A booking is added.",
    ),
    _event(
        "reservation.updated",
        "Reservation updated",
        "Reservations",
        "reservation",
        _RES_SCOPES,
        _RES_REFS,
        "Dates, unit or details of a booking change.",
    ),
    _event(
        "reservation.cancelled",
        "Reservation cancelled",
        "Reservations",
        "reservation",
        _RES_SCOPES,
        _RES_REFS,
        "A booking is cancelled.",
    ),
    _event(
        "reservation.checked_in",
        "Guest checked in",
        "Reservations",
        "reservation",
        _RES_SCOPES,
        _RES_REFS,
        "A stay starts.",
    ),
    _event(
        "reservation.checked_out",
        "Guest checked out",
        "Reservations",
        "reservation",
        _RES_SCOPES,
        _RES_REFS,
        "A stay ends. (Turnover cleaning is created by StayOps itself.)",
    ),
    _event(
        "task.created",
        "Task created",
        "Tasks",
        "task",
        _TASK_SCOPES,
        _TASK_REFS,
        "Any task is created.",
    ),
    _event(
        "task.assigned",
        "Task assigned",
        "Tasks",
        "task",
        _TASK_SCOPES,
        _TASK_REFS,
        "A task is assigned or reassigned.",
    ),
    _event(
        "task.completed",
        "Task completed",
        "Tasks",
        "task",
        _TASK_SCOPES,
        _TASK_REFS,
        "A task is marked done.",
    ),
    _event(
        "task.cancelled",
        "Task cancelled",
        "Tasks",
        "task",
        _TASK_SCOPES,
        _TASK_REFS,
        "A task is cancelled.",
    ),
    _event(
        "cleaning.created",
        "Cleaning created",
        "Cleaning",
        "cleaning",
        _CLEAN_SCOPES,
        _TASK_REFS | {"cleaning"},
        "A turnover cleaning is scheduled.",
    ),
    _event(
        "cleaning.completed",
        "Cleaning completed",
        "Cleaning",
        "cleaning",
        _CLEAN_SCOPES,
        _TASK_REFS | {"cleaning"},
        "A cleaner finishes.",
    ),
    _event(
        "cleaning.approved",
        "Cleaning approved",
        "Cleaning",
        "cleaning",
        _CLEAN_SCOPES,
        _TASK_REFS | {"cleaning"},
        "An inspection passes.",
    ),
    _event(
        "cleaning.failed",
        "Inspection failed",
        "Cleaning",
        "cleaning",
        _CLEAN_SCOPES,
        _TASK_REFS | {"cleaning"},
        "An inspection fails (the cleaning is reopened automatically).",
    ),
    _event(
        "maintenance.created",
        "Work order created",
        "Maintenance",
        "work_order",
        _WO_SCOPES,
        _WO_REFS,
        "A maintenance issue is reported.",
    ),
    _event(
        "maintenance.assigned",
        "Work order assigned",
        "Maintenance",
        "work_order",
        _WO_SCOPES,
        _WO_REFS,
        "A work order is assigned.",
    ),
    _event(
        "maintenance.resolved",
        "Work order resolved",
        "Maintenance",
        "work_order",
        _WO_SCOPES,
        _WO_REFS,
        "The fix is done.",
    ),
    _event(
        "maintenance.closed",
        "Work order closed",
        "Maintenance",
        "work_order",
        _WO_SCOPES,
        _WO_REFS,
        "A work order is closed.",
    ),
    _event(
        "conversation.created",
        "Conversation started",
        "Guest experience",
        "conversation",
        {"conversation", "property"},
        {"conversation", "reservation", "property", "unit"},
        "A new guest conversation.",
    ),
    _event(
        "message.created",
        "Message received",
        "Guest experience",
        "message",
        {"message", "conversation", "property"},
        {"conversation", "reservation", "property", "unit"},
        "A message is added to a conversation.",
    ),
    _event(
        "guest_request.created",
        "Guest request raised",
        "Guest experience",
        "guest_request",
        _GR_SCOPES,
        _GR_REFS,
        "A guest (or the team) raises a request.",
    ),
    _event(
        "guest_request.updated",
        "Guest request updated",
        "Guest experience",
        "guest_request",
        _GR_SCOPES,
        _GR_REFS,
        "A request changes status.",
    ),
    _event(
        "guest_request.resolved",
        "Guest request resolved",
        "Guest experience",
        "guest_request",
        _GR_SCOPES,
        _GR_REFS,
        "A request is resolved.",
    ),
    TriggerDef(
        type="schedule.task_overdue",
        label="Task overdue",
        category="Scheduled",
        kind="schedule",
        subject="task",
        scopes=frozenset(_TASK_SCOPES),
        refs=frozenset(_TASK_REFS),
        description="An open task passes its due time (checked every minute; once per due time).",
        config_model=TaskOverdueConfig,
    ),
    TriggerDef(
        type="schedule.cleaning_not_ready",
        label="Cleaning not finished before check-in",
        category="Scheduled",
        kind="schedule",
        subject="task",
        scopes=frozenset(_CLEAN_SCOPES | {"arrival"}),
        refs=frozenset(_TASK_REFS | {"cleaning"}),
        description=(
            "A turnover cleaning is still unfinished shortly before the next guest's check-in."
        ),
        config_model=CleaningNotReadyConfig,
    ),
    TriggerDef(
        type="schedule.maintenance_overdue",
        label="Work order unresolved too long",
        category="Scheduled",
        kind="schedule",
        subject="work_order",
        scopes=frozenset(_WO_SCOPES),
        refs=frozenset(_WO_REFS),
        description="A work order of the chosen priorities stays unresolved past a threshold.",
        config_model=MaintenanceOverdueConfig,
    ),
    TriggerDef(
        type="schedule.task_unassigned",
        label="Task unassigned too long",
        category="Scheduled",
        kind="schedule",
        subject="task",
        scopes=frozenset(_TASK_SCOPES),
        refs=frozenset(_TASK_REFS),
        description="An open task has had no assignee for longer than a threshold.",
        config_model=TaskUnassignedConfig,
    ),
    TriggerDef(
        type="schedule.daily_summary",
        label="Morning operations summary",
        category="Scheduled",
        kind="schedule",
        subject="property",
        scopes=frozenset({"property", "summary"}),
        refs=frozenset({"property"}),
        description="Once a day per property, at a local time (property timezone, DST-aware).",
        config_model=DailySummaryConfig,
    ),
)
TRIGGERS_BY_TYPE: dict[str, TriggerDef] = {t.type: t for t in TRIGGERS}
EVENT_TRIGGER_TYPES = frozenset(t.type for t in TRIGGERS if t.kind == "event")
SCHEDULE_TRIGGER_TYPES = frozenset(t.type for t in TRIGGERS if t.kind == "schedule")


def fields_for(trigger: TriggerDef) -> list[FieldDef]:
    return [f for f in FIELDS if f.scope in trigger.scopes]


# ----------------------------------------------------------------- limits

MAX_NODES = 25
MAX_EDGES = 40
MAX_CONDITIONS = 8
MAX_ACTIONS = 12
MAX_RULES_PER_CONDITION = 10
MAX_CONDITION_DEPTH = 2  # a group may contain groups, which may only contain rules
MAX_LIST_VALUES = 20


def catalog_payload(action_defs: list[dict[str, Any]]) -> dict[str, Any]:
    """The catalog served to the editor and the AI drafter."""
    return {
        "triggers": [
            {
                "type": t.type,
                "label": t.label,
                "category": t.category,
                "kind": t.kind,
                "subject": t.subject,
                "description": t.description,
                "scopes": sorted(t.scopes),
                "refs": sorted(t.refs),
                "config_schema": t.config_model.model_json_schema(),
            }
            for t in TRIGGERS
        ],
        "fields": [
            {
                "path": f.path,
                "label": f.label,
                "type": f.type,
                "scope": f.scope,
                "values": list(f.values),
                "unit": f.unit,
                "ops": [o.value for o in f.ops],
            }
            for f in FIELDS
        ],
        "operators": [{"op": o.value, "label": OP_LABELS[o]} for o in Op],
        "actions": action_defs,
        "limits": {
            "max_nodes": MAX_NODES,
            "max_conditions": MAX_CONDITIONS,
            "max_actions": MAX_ACTIONS,
            "max_rules_per_condition": MAX_RULES_PER_CONDITION,
            "max_condition_depth": MAX_CONDITION_DEPTH,
        },
        "alert_severities": [s.value for s in AlertSeverity],
    }
