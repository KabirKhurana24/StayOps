"""Dry run: what *would* this workflow do for a real event or record?

Simulation uses the same validator, subject builder, condition evaluator and
action ``plan`` functions as live execution — but never calls an action's
``execute``. ``plan`` is read-only by contract; as a second line of defence the
whole simulation runs in a transaction that is always rolled back. Nothing is
recorded as an execution, and no message, task, alert or block is created.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.context import Actor, TenantContext
from app.auth.permissions import Permission
from app.automation.actions import ActionEnv, get_action
from app.automation.catalog import TRIGGERS_BY_TYPE, TriggerDef
from app.automation.context import Subject, build_subject, event_actor_type, subject_labels
from app.automation.definition import ActionNode, ConditionNode, evaluate, render_config
from app.automation.runner import node_label, safe_error
from app.automation.schemas import SampleOut, SimStep, SimulationOut
from app.automation.service import issues_out
from app.automation.triggers import property_filter_passes
from app.automation.validation import approval_policy, validate
from app.core.errors import AppError, DomainValidationError, NotFoundError
from app.core.time import utc_now
from app.events.models import Event
from app.maintenance.models import WorkOrder
from app.organizations.models import Role
from app.properties.models import Property
from app.tasks.models import Task


def _subject(
    session: Session,
    ctx: TenantContext,
    trigger: TriggerDef,
    event_id: uuid.UUID | None,
    entity_id: uuid.UUID | None,
) -> tuple[Subject, bool, str]:
    now = utc_now()
    if event_id is not None:
        event = session.scalars(
            select(Event).where(Event.id == event_id, Event.organization_id == ctx.organization_id)
        ).first()
        if event is None:
            raise NotFoundError("Event not found.")
        changes = (
            event.payload.get("changes") if isinstance(event.payload.get("changes"), dict) else None
        )
        subject = build_subject(
            session, ctx.organization_id, trigger.subject, event.aggregate_id, now, changes
        )
        if subject is None:
            raise NotFoundError("The event's record no longer exists.")
        subject.fields["event.actor_type"] = event_actor_type(session, event.id)
        matches = trigger.kind == "event" and event.event_type == trigger.type
        why = (
            f"Event {event.event_type} matches the trigger."
            if matches
            else f"Event {event.event_type} doesn't match the trigger “{trigger.label}”."
        )
        return subject, matches, why
    if entity_id is None:
        raise DomainValidationError("Choose an event or a record to simulate with.")
    subject = build_subject(session, ctx.organization_id, trigger.subject, entity_id, now)
    if subject is None:
        raise NotFoundError("Record not found.")
    return subject, True, f"Simulating “{trigger.label}” for {subject.label}."


def simulate(
    session: Session,
    ctx: TenantContext,
    raw: dict[str, Any],
    *,
    event_id: uuid.UUID | None,
    entity_id: uuid.UUID | None,
    workflow_id: uuid.UUID,
    workflow_name: str,
) -> SimulationOut:
    ctx.require(Permission.AUTOMATIONS_MANAGE)
    try:
        return _simulate(session, ctx, raw, event_id, entity_id, workflow_id, workflow_name)
    finally:
        session.rollback()  # defence in depth: a dry run never persists anything


def _simulate(
    session: Session,
    ctx: TenantContext,
    raw: dict[str, Any],
    event_id: uuid.UUID | None,
    entity_id: uuid.UUID | None,
    workflow_id: uuid.UUID,
    workflow_name: str,
) -> SimulationOut:
    report = validate(raw, session=session, organization_id=ctx.organization_id)
    definition, trigger = report.definition, report.trigger
    if definition is None or trigger is None or definition.trigger is None:
        return SimulationOut(
            valid=False,
            issues=issues_out(report),
            trigger_matches=False,
            subject=None,
            fields={},
            steps=[],
            summary="Fix the workflow's structure before simulating.",
        )
    subject, matches, why = _subject(session, ctx, trigger, event_id, entity_id)
    if matches and not property_filter_passes(definition, trigger, subject):
        matches, why = False, "The record's property is outside this trigger's property filter."
    steps: list[SimStep] = [
        SimStep(
            node_id=definition.trigger.id,
            kind="trigger",
            type=trigger.type,
            label=trigger.label,
            outcome="matched" if matches else "not_matched",
            detail=why,
        )
    ]
    subject_out = {
        "entity_type": subject.entity_type,
        "entity_id": str(subject.entity_id),
        "label": subject.label,
    }
    if not matches:
        return SimulationOut(
            valid=report.valid,
            issues=issues_out(report),
            trigger_matches=False,
            subject=subject_out,
            fields=subject.fields,
            steps=steps,
            summary="The trigger wouldn't fire, so nothing would happen.",
        )

    sim_ctx = TenantContext(
        organization_id=ctx.organization_id,
        role=Role.OPERATOR,
        actor=Actor.system(f"Autopilot · {workflow_name}"[:320]),
        source="automation",
    )
    env = ActionEnv(
        session=session,
        ctx=sim_ctx,
        execution_id=None,
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        step_id="",
        refs=subject.refs,
        fields=subject.fields,
        now=utc_now(),
        simulation=True,
    )
    visited = {definition.trigger.id}
    current = definition.next_id(definition.trigger.id)
    would_run = approvals = 0
    for _ in range(len(definition.nodes) + 1):
        if current is None or current in visited:
            break
        node = definition.node(current)
        if node is None:
            break
        visited.add(current)
        if isinstance(node, ConditionNode):
            passed, trace = evaluate(node.condition, subject.fields)
            steps.append(
                SimStep(
                    node_id=node.id,
                    kind="condition",
                    type="condition",
                    label=node_label(node),
                    outcome="passed" if passed else "failed",
                    detail="Condition passed → “yes” branch"
                    if passed
                    else "Condition not met → “no” branch",
                    data={"rules": trace},
                )
            )
            current = definition.next_id(node.id, "true" if passed else "false")
            continue
        if not isinstance(node, ActionNode):
            break
        action = get_action(node.type)
        label = node_label(node, action)
        try:
            if action is None:
                raise DomainValidationError(f"“{node.type}” is not a supported action.")
            inputs = action.input_model.model_validate(render_config(node.config, subject.fields))
            plan = action.plan(_with_step(env, node.id), inputs)
        except (AppError, ValidationError, ValueError) as exc:
            steps.append(
                SimStep(
                    node_id=node.id,
                    kind="action",
                    type=node.type,
                    label=label,
                    outcome="invalid",
                    detail=safe_error(exc)[1],
                )
            )
            if definition.settings.on_error == "stop":
                break
            current = definition.next_id(node.id)
            continue
        policy = approval_policy(node)
        outcome = (
            "requires_approval"
            if policy == "required"
            else "would_stop"
            if action.terminal
            else "would_execute"
        )
        would_run += outcome == "would_execute"
        approvals += outcome == "requires_approval"
        steps.append(
            SimStep(
                node_id=node.id,
                kind="action",
                type=node.type,
                label=label,
                outcome=outcome,
                detail=plan.summary,
                data={
                    "inputs": inputs.model_dump(mode="json"),
                    "expected_effect": plan.expected_effect,
                    "risk": action.effective_risk(inputs),
                    "guest_facing": plan.guest_facing,
                    "target": plan.entity_label,
                },
            )
        )
        if outcome == "requires_approval":
            # Live execution pauses here; later steps depend on the decision.
            current = definition.next_id(node.id)
            continue
        if action.terminal:
            break
        current = definition.next_id(node.id)

    for node in definition.nodes:
        if node.id not in visited and isinstance(node, ActionNode | ConditionNode):
            steps.append(
                SimStep(
                    node_id=node.id,
                    kind="condition" if isinstance(node, ConditionNode) else "action",
                    type="condition" if isinstance(node, ConditionNode) else node.type,
                    label=node_label(
                        node, get_action(node.type) if isinstance(node, ActionNode) else None
                    ),
                    outcome="skipped",
                    detail="Not on the path taken for this sample.",
                )
            )
    parts = [f"{would_run} action(s) would run automatically"]
    if approvals:
        parts.append(f"{approvals} would wait for approval")
    return SimulationOut(
        valid=report.valid,
        issues=issues_out(report),
        trigger_matches=True,
        subject=subject_out,
        fields=subject.fields,
        steps=steps,
        summary="; ".join(parts) + ". Simulation only — nothing was changed.",
    )


def _with_step(env: ActionEnv, step_id: str) -> ActionEnv:
    from dataclasses import replace

    return replace(env, step_id=step_id)


def samples(
    session: Session, ctx: TenantContext, trigger_type: str, limit: int = 15
) -> list[SampleOut]:
    """Recent real events (event triggers) or records (scheduled) to simulate with."""
    from app.automation.scheduler import SCANNERS

    ctx.require(Permission.AUTOMATIONS_READ)
    trigger = TRIGGERS_BY_TYPE.get(trigger_type)
    if trigger is None:
        return []
    now = utc_now()
    if trigger.kind == "event":
        events = session.scalars(
            select(Event)
            .where(Event.organization_id == ctx.organization_id, Event.event_type == trigger_type)
            .order_by(Event.occurred_at.desc())
            .limit(limit)
        ).all()
        labels = subject_labels(
            session, ctx.organization_id, trigger.subject, [e.aggregate_id for e in events]
        )
        return [
            SampleOut(
                kind="event", id=e.id, label=labels[e.aggregate_id], occurred_at=e.occurred_at
            )
            for e in events
            if e.aggregate_id in labels
        ]
    config = trigger.config_model()
    occurrences = SCANNERS[trigger.type](session, ctx.organization_id, config, now)[:limit]
    labels = subject_labels(
        session, ctx.organization_id, trigger.subject, [o.entity_id for o in occurrences]
    )
    out = [
        SampleOut(
            kind="entity", id=o.entity_id, label=labels[o.entity_id], occurred_at=o.scheduled_for
        )
        for o in occurrences
        if o.entity_id in labels
    ]
    if out:
        return out
    # Nothing is due right now: offer recent records of the trigger's subject instead.
    model: Any = {"task": Task, "work_order": WorkOrder, "property": Property}[trigger.subject]
    ids = session.scalars(
        select(model.id)
        .where(model.organization_id == ctx.organization_id)
        .order_by(model.created_at.desc())
        .limit(limit)
    ).all()
    labels = subject_labels(session, ctx.organization_id, trigger.subject, list(ids))
    return [SampleOut(kind="entity", id=i, label=labels[i]) for i in ids if i in labels]
