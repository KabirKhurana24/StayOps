"""Scheduled triggers, evaluated by the existing worker (no separate scheduler).

Each scheduled trigger type has a *scanner* that finds due occurrences for one
workflow, e.g. "task T is overdue for due time D". An occurrence becomes a PENDING
execution with ``dedupe_key = schedule:<occurrence key>`` — unique per workflow in
the database — so however often the scan runs, and across worker restarts or
several workers, each occurrence runs once. The execution row *is* the persisted
occurrence; it is written before anything executes.

Supported (documented, fixed set — no cron expressions):

* ``schedule.task_overdue``        open task past due (+ N minutes); once per due time
* ``schedule.cleaning_not_ready``  turnover cleaning unfinished N minutes before the
                                   next check-in; once per check-in
* ``schedule.maintenance_overdue`` work order of given priorities unresolved N hours
* ``schedule.task_unassigned``     open task without assignee for N minutes
* ``schedule.daily_summary``       once per property per local day at HH:MM in the
                                   property's timezone (UTC stored; DST-aware)

Scans are bounded (``SCAN_LIMIT`` per workflow per pass) and look back a limited
window, so activating a workflow doesn't replay ancient history.
"""

from __future__ import annotations

import time as monotonic
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.automation.catalog import (
    SCHEDULE_TRIGGER_TYPES,
    TRIGGERS_BY_TYPE,
    CleaningNotReadyConfig,
    DailySummaryConfig,
    MaintenanceOverdueConfig,
    TaskOverdueConfig,
    TaskUnassignedConfig,
    TriggerConfig,
)
from app.automation.context import build_subject
from app.automation.definition import Definition
from app.automation.models import Workflow, WorkflowStatus, WorkflowVersion
from app.automation.triggers import start_execution
from app.cleaning.models import FINISHED_STATUSES, Cleaning
from app.config.settings import get_settings
from app.core.logging import get_logger
from app.core.time import local_to_utc, utc_now
from app.maintenance.models import ACTIVE_MAINTENANCE_STATUSES, WorkOrder
from app.operations.turnover import next_arrival
from app.properties.models import Property
from app.tasks.models import OPEN_TASK_STATUSES, Task, TaskType

logger = get_logger("stayops.automation.scheduler")

SCAN_LIMIT = 100
LOOKBACK = timedelta(days=7)
DAILY_CATCH_UP = timedelta(hours=6)  # a summary missed by a down worker still runs late


@dataclass(frozen=True, slots=True)
class Occurrence:
    entity_type: str
    entity_id: uuid.UUID
    key: str
    scheduled_for: datetime
    extra_fields: dict[str, Any] | None = None


Scanner = Callable[[Session, uuid.UUID, Any, datetime], list[Occurrence]]


def _props(config: TriggerConfig) -> list[uuid.UUID]:
    return list(config.property_ids)


def scan_task_overdue(
    session: Session, org: uuid.UUID, config: TaskOverdueConfig, now: datetime
) -> list[Occurrence]:
    threshold = now - timedelta(minutes=config.minutes_overdue)
    query = select(Task.id, Task.due_at).where(
        Task.organization_id == org,
        Task.status.in_(OPEN_TASK_STATUSES),
        Task.due_at.is_not(None),
        Task.due_at <= threshold,
        Task.due_at >= now - LOOKBACK,
    )
    if config.task_types:
        query = query.where(Task.task_type.in_(config.task_types))
    if config.property_ids:
        query = query.where(Task.property_id.in_(_props(config)))
    rows = session.execute(query.order_by(Task.due_at).limit(SCAN_LIMIT)).all()
    return [
        Occurrence(
            "task",
            tid,
            f"task_overdue:{tid}:{due.isoformat()}",
            due + timedelta(minutes=config.minutes_overdue),
        )
        for tid, due in rows
        if due is not None
    ]


def scan_cleaning_not_ready(
    session: Session, org: uuid.UUID, config: CleaningNotReadyConfig, now: datetime
) -> list[Occurrence]:
    # A turnover cleaning's due time is the next arrival's check-in (operations.turnover).
    query = (
        select(Task.id, Task.due_at, Task.unit_id, Task.reservation_id)
        .join(Cleaning, Cleaning.task_id == Task.id)
        .where(
            Task.organization_id == org,
            Task.task_type == TaskType.TURNOVER_CLEANING,
            Task.status.in_(OPEN_TASK_STATUSES),
            Cleaning.status.not_in(FINISHED_STATUSES),
            Task.due_at.is_not(None),
            Task.due_at > now,
            Task.due_at <= now + timedelta(minutes=config.minutes_before_check_in),
        )
    )
    if config.property_ids:
        query = query.where(Task.property_id.in_(_props(config)))
    out = []
    for tid, due, unit_id, _ in session.execute(
        query.order_by(Task.due_at).limit(SCAN_LIMIT)
    ).all():
        if due is None:
            continue
        arrival = (
            next_arrival(session, org, unit_id, due - timedelta(minutes=1)) if unit_id else None
        )
        if arrival is None or arrival.check_in != due:
            continue  # the deadline is the default check-in time, not a guest arriving
        out.append(
            Occurrence(
                "task",
                tid,
                f"cleaning_not_ready:{tid}:{due.isoformat()}",
                due - timedelta(minutes=config.minutes_before_check_in),
                {
                    "arrival.minutes_until_check_in": int((due - now).total_seconds() // 60),
                    "arrival.confirmation_code": arrival.confirmation_code,
                },
            )
        )
    return out


def scan_maintenance_overdue(
    session: Session, org: uuid.UUID, config: MaintenanceOverdueConfig, now: datetime
) -> list[Occurrence]:
    threshold = now - timedelta(hours=config.hours_open)
    query = select(WorkOrder.id, WorkOrder.created_at).where(
        WorkOrder.organization_id == org,
        WorkOrder.status.in_(ACTIVE_MAINTENANCE_STATUSES),
        WorkOrder.priority.in_(config.priorities),
        WorkOrder.created_at <= threshold,
        WorkOrder.created_at >= now - LOOKBACK - timedelta(hours=config.hours_open),
    )
    if config.property_ids:
        query = query.where(WorkOrder.property_id.in_(_props(config)))
    rows = session.execute(query.order_by(WorkOrder.created_at).limit(SCAN_LIMIT)).all()
    return [
        Occurrence(
            "work_order",
            oid,
            f"maintenance_overdue:{oid}:{config.hours_open}h",
            created + timedelta(hours=config.hours_open),
        )
        for oid, created in rows
    ]


def scan_task_unassigned(
    session: Session, org: uuid.UUID, config: TaskUnassignedConfig, now: datetime
) -> list[Occurrence]:
    threshold = now - timedelta(minutes=config.minutes_unassigned)
    query = select(Task.id, Task.created_at).where(
        Task.organization_id == org,
        Task.status.in_(OPEN_TASK_STATUSES),
        Task.assigned_to.is_(None),
        Task.created_at <= threshold,
        Task.created_at >= now - LOOKBACK,
    )
    if config.priorities:
        query = query.where(Task.priority.in_(config.priorities))
    if config.property_ids:
        query = query.where(Task.property_id.in_(_props(config)))
    rows = session.execute(query.order_by(Task.created_at).limit(SCAN_LIMIT)).all()
    return [
        Occurrence(
            "task",
            tid,
            f"task_unassigned:{tid}:{config.minutes_unassigned}m",
            created + timedelta(minutes=config.minutes_unassigned),
        )
        for tid, created in rows
    ]


def daily_occurrence(tz: str, local_time: str, now: datetime) -> tuple[str, datetime] | None:
    """(local date, UTC instant) of today's run in ``tz`` if it is due now.

    Uses the property's wall clock: on DST change days the UTC instant shifts, and a
    wall time that doesn't exist (spring-forward gap) resolves forward by zoneinfo's
    rules. Each local date yields exactly one occurrence key.
    """
    hour, minute = (int(p) for p in local_time.split(":"))
    zone = ZoneInfo(tz)
    today = now.astimezone(zone).date()
    for day in (today, today - timedelta(days=1)):
        at = local_to_utc(day, time(hour, minute), tz)
        if at <= now < at + DAILY_CATCH_UP:
            return day.isoformat(), at
    return None


def scan_daily_summary(
    session: Session, org: uuid.UUID, config: DailySummaryConfig, now: datetime
) -> list[Occurrence]:
    query = select(Property.id, Property.timezone).where(Property.organization_id == org)
    if config.property_ids:
        query = query.where(Property.id.in_(_props(config)))
    out = []
    for pid, tz in session.execute(query.limit(SCAN_LIMIT)).all():
        due = daily_occurrence(tz, config.local_time, now)
        if due is not None:
            out.append(Occurrence("property", pid, f"daily_summary:{pid}:{due[0]}", due[1]))
    return out


SCANNERS: dict[str, Scanner] = {
    "schedule.task_overdue": scan_task_overdue,
    "schedule.cleaning_not_ready": scan_cleaning_not_ready,
    "schedule.maintenance_overdue": scan_maintenance_overdue,
    "schedule.task_unassigned": scan_task_unassigned,
    "schedule.daily_summary": scan_daily_summary,
}
assert set(SCANNERS) == SCHEDULE_TRIGGER_TYPES  # noqa: S101 — catalog and scanners agree


def scheduled_workflows(
    session: Session, organization_id: uuid.UUID | None = None
) -> list[tuple[Workflow, WorkflowVersion]]:
    query = (
        select(Workflow, WorkflowVersion)
        .join(
            WorkflowVersion,
            (WorkflowVersion.workflow_id == Workflow.id)
            & (WorkflowVersion.version == Workflow.current_version),
        )
        .where(
            Workflow.status == WorkflowStatus.ACTIVE,
            Workflow.published_trigger_type.in_(SCHEDULE_TRIGGER_TYPES),
        )
    )
    if organization_id is not None:
        query = query.where(Workflow.organization_id == organization_id)
    return [(w, v) for w, v in session.execute(query).all()]


def occurrences_for(
    session: Session, workflow: Workflow, version: WorkflowVersion, now: datetime
) -> list[Occurrence]:
    definition = Definition.model_validate(version.definition)
    node = definition.trigger
    trigger = TRIGGERS_BY_TYPE.get(version.trigger_type)
    if node is None or trigger is None:
        return []
    config = trigger.config_model.model_validate(node.config)
    return SCANNERS[trigger.type](session, workflow.organization_id, config, now)


def tick(
    session: Session, now: datetime | None = None, organization_id: uuid.UUID | None = None
) -> int:
    """Record every due occurrence as a PENDING execution. Returns how many were new."""
    now = now or utc_now()
    created = 0
    for workflow, version in scheduled_workflows(session, organization_id):
        trigger = TRIGGERS_BY_TYPE[version.trigger_type]
        for occ in occurrences_for(session, workflow, version, now):
            subject = build_subject(
                session, workflow.organization_id, occ.entity_type, occ.entity_id, now
            )
            if subject is None:
                continue
            if occ.extra_fields:
                subject.fields |= occ.extra_fields
            if start_execution(
                session,
                workflow=workflow,
                version=version,
                trigger=trigger,
                subject=subject,
                dedupe_key=f"schedule:{occ.key}"[:200],
                trigger_info={
                    "type": trigger.type,
                    "occurrence": occ.key,
                    "scheduled_for": occ.scheduled_for.isoformat(),
                },
                triggering_event_id=None,
                correlation_id=occ.key[:100],
                now=now,
            ):
                created += 1
    session.commit()
    if created:
        logger.info("automation.schedule_occurrences", created=created)
    return created


_last_tick = 0.0


def maybe_tick(session: Session) -> int:
    """Rate-limited tick for the worker loop (correctness never depends on timing)."""
    global _last_tick
    interval = get_settings().automation_schedule_interval_seconds
    if monotonic.monotonic() - _last_tick < interval:
        return 0
    _last_tick = monotonic.monotonic()
    return tick(session)
