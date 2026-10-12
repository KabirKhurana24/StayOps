"""Event triggers: persisted domain events → idempotent workflow executions.

The subscriber runs inside the event's processing transaction (like every other
handler) and only *records* executions; the drain hook runs them afterwards, so
neither the API request nor other handlers wait for a workflow.

* Only ACTIVE workflows of the event's organization whose **published** trigger
  matches are considered; each runs its pinned published version.
* The subject is reloaded from the database scoped to the organization — the
  event payload never decides what a workflow may touch.
* ``dedupe_key = event:<event id>`` is unique per workflow: re-delivery of the
  event (retries, lease takeover) can never start a second run.
* Events caused by Autopilot itself (``source == "automation"``) never trigger
  workflows, so workflows cannot feed each other in loops.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.automation.catalog import EVENT_TRIGGER_TYPES, TRIGGERS_BY_TYPE, TriggerDef
from app.automation.context import Subject, build_subject, event_actor_type
from app.automation.definition import Definition
from app.automation.models import (
    Execution,
    ExecutionStatus,
    StepExecution,
    StepStatus,
    Workflow,
    WorkflowStatus,
    WorkflowVersion,
)
from app.automation.runner import drain_hook
from app.core.logging import get_logger
from app.core.time import utc_now
from app.events.dispatcher import register_drain_hook, registry
from app.events.types import EventEnvelope

logger = get_logger("stayops.automation.triggers")

AUTOMATION_SOURCE = "automation"


def property_filter_passes(definition: Definition, trigger: TriggerDef, subject: Subject) -> bool:
    node = definition.trigger
    config = trigger.config_model.model_validate(node.config if node else {})
    wanted = set(config.property_ids)
    return not wanted or subject.property_id in wanted


def start_execution(
    session: Session,
    *,
    workflow: Workflow,
    version: WorkflowVersion,
    trigger: TriggerDef,
    subject: Subject,
    dedupe_key: str,
    trigger_info: dict[str, Any],
    triggering_event_id: uuid.UUID | None,
    correlation_id: str | None,
    now: datetime,
) -> uuid.UUID | None:
    """Record a PENDING execution (and its trigger step). None if it already exists."""
    execution_id = session.execute(
        insert(Execution)
        .values(
            organization_id=workflow.organization_id,
            workflow_id=workflow.id,
            workflow_version_id=version.id,
            workflow_version=version.version,
            trigger_type=trigger.type,
            triggering_event_id=triggering_event_id,
            dedupe_key=dedupe_key,
            status=ExecutionStatus.PENDING,
            entity_type=subject.entity_type,
            entity_id=subject.entity_id,
            entity_label=subject.label[:200],
            execution_context=subject.context(trigger_info),
            correlation_id=correlation_id,
            started_at=now,
        )
        .on_conflict_do_nothing(index_elements=[Execution.workflow_id, Execution.dedupe_key])
        .returning(Execution.id)
    ).scalar_one_or_none()
    if execution_id is None:
        logger.info(
            "automation.duplicate_trigger", workflow_id=str(workflow.id), dedupe_key=dedupe_key
        )
        return None
    definition = Definition.model_validate(version.definition)
    node = definition.trigger
    session.add(
        StepExecution(
            organization_id=workflow.organization_id,
            execution_id=execution_id,
            step_id=node.id if node else "trigger",
            step_type="trigger",
            label=f"{trigger.label}: {subject.label}"[:200],
            status=StepStatus.COMPLETED,
            started_at=now,
            completed_at=now,
            output_summary={"matched": True, **trigger_info},
        )
    )
    session.flush()
    return execution_id


def active_workflows(
    session: Session, organization_id: uuid.UUID, trigger_type: str
) -> list[tuple[Workflow, WorkflowVersion]]:
    rows = session.execute(
        select(Workflow, WorkflowVersion)
        .join(
            WorkflowVersion,
            (WorkflowVersion.workflow_id == Workflow.id)
            & (WorkflowVersion.version == Workflow.current_version),
        )
        .where(
            Workflow.organization_id == organization_id,
            Workflow.status == WorkflowStatus.ACTIVE,
            Workflow.published_trigger_type == trigger_type,
            WorkflowVersion.organization_id == organization_id,
        )
        .order_by(Workflow.created_at)
    ).all()
    return [(w, v) for w, v in rows]


@registry.subscribe("*", name="automation.triggers")
def on_event(session: Session, event: EventEnvelope) -> None:
    if event.event_type not in EVENT_TRIGGER_TYPES or event.source == AUTOMATION_SOURCE:
        return
    candidates = active_workflows(session, event.organization_id, event.event_type)
    if not candidates:
        return
    trigger = TRIGGERS_BY_TYPE[event.event_type]
    now = utc_now()
    changes = (
        event.payload.get("changes") if isinstance(event.payload.get("changes"), dict) else None
    )
    subject = build_subject(
        session, event.organization_id, trigger.subject, event.aggregate_id, now, changes
    )
    if subject is None:
        return  # not (or no longer) an entity of this organization
    subject.fields["event.actor_type"] = event_actor_type(session, event.id)
    info = {
        "type": event.event_type,
        "event_id": str(event.id),
        "occurred_at": event.occurred_at.isoformat(),
    }
    for workflow, version in candidates:
        definition = Definition.model_validate(version.definition)
        if not property_filter_passes(definition, trigger, subject):
            continue
        start_execution(
            session,
            workflow=workflow,
            version=version,
            trigger=trigger,
            subject=subject,
            dedupe_key=f"event:{event.id}",
            trigger_info=info,
            triggering_event_id=event.id,
            correlation_id=str(event.id),
            now=now,
        )


register_drain_hook("automation", drain_hook)
