from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.core.time import utc_now


class EventType(StrEnum):
    ORGANIZATION_CREATED = "organization.created"
    PROPERTY_CREATED = "property.created"
    PROPERTY_UPDATED = "property.updated"
    UNIT_CREATED = "unit.created"
    UNIT_BLOCKED = "unit.blocked"
    UNIT_UNBLOCKED = "unit.unblocked"
    GUEST_CREATED = "guest.created"
    GUEST_UPDATED = "guest.updated"
    RESERVATION_CREATED = "reservation.created"
    RESERVATION_UPDATED = "reservation.updated"
    RESERVATION_CANCELLED = "reservation.cancelled"
    RESERVATION_CHECKED_IN = "reservation.checked_in"
    RESERVATION_CHECKED_OUT = "reservation.checked_out"
    TASK_CREATED = "task.created"
    TASK_UPDATED = "task.updated"
    TASK_ASSIGNED = "task.assigned"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_CANCELLED = "task.cancelled"
    TASK_REOPENED = "task.reopened"
    CLEANING_CREATED = "cleaning.created"
    CLEANING_ASSIGNED = "cleaning.assigned"
    CLEANING_STARTED = "cleaning.started"
    CLEANING_UPDATED = "cleaning.updated"
    CLEANING_COMPLETED = "cleaning.completed"
    CLEANING_INSPECTION_REQUIRED = "cleaning.inspection_required"
    CLEANING_APPROVED = "cleaning.approved"
    CLEANING_FAILED = "cleaning.failed"
    MAINTENANCE_CREATED = "maintenance.created"
    MAINTENANCE_UPDATED = "maintenance.updated"
    MAINTENANCE_ASSIGNED = "maintenance.assigned"
    MAINTENANCE_STARTED = "maintenance.started"
    MAINTENANCE_BLOCKED = "maintenance.blocked"
    MAINTENANCE_RESOLVED = "maintenance.resolved"
    MAINTENANCE_CLOSED = "maintenance.closed"
    CONVERSATION_CREATED = "conversation.created"
    CONVERSATION_UPDATED = "conversation.updated"
    CONVERSATION_ASSIGNED = "conversation.assigned"
    MESSAGE_CREATED = "message.created"
    MESSAGE_READ = "message.read"
    GUEST_REQUEST_CREATED = "guest_request.created"
    GUEST_REQUEST_UPDATED = "guest_request.updated"
    GUEST_REQUEST_RESOLVED = "guest_request.resolved"
    GUEST_REQUEST_CLOSED = "guest_request.closed"
    KNOWLEDGE_CREATED = "knowledge.created"
    KNOWLEDGE_UPDATED = "knowledge.updated"
    KNOWLEDGE_ARCHIVED = "knowledge.archived"
    GUEST_PORTAL_LINK_CREATED = "guest_portal.link_created"
    GUEST_PORTAL_LINK_REVOKED = "guest_portal.link_revoked"


class AggregateType(StrEnum):
    ORGANIZATION = "organization"
    PROPERTY = "property"
    UNIT = "unit"
    GUEST = "guest"
    RESERVATION = "reservation"
    TASK = "task"
    CLEANING = "cleaning"
    MAINTENANCE = "maintenance"
    CONVERSATION = "conversation"
    MESSAGE = "message"
    GUEST_REQUEST = "guest_request"
    KNOWLEDGE = "knowledge"
    GUEST_PORTAL_LINK = "guest_portal_link"


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """An immutable fact about something that happened in one organization.

    ``idempotency_key`` must be deterministic for the underlying change (e.g.
    ``reservation.updated:<id>:v3`` or a client-supplied Idempotency-Key) so a
    retried command cannot emit the event twice.
    """

    event_type: EventType
    aggregate_type: AggregateType
    aggregate_id: uuid.UUID
    idempotency_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """Read-only view of a persisted event handed to subscribers."""

    id: uuid.UUID
    organization_id: uuid.UUID
    event_type: str
    aggregate_type: str
    aggregate_id: uuid.UUID
    occurred_at: datetime
    payload: dict[str, Any]
    source: str
    actor_id: uuid.UUID | None
    attempt: int
