"""Cleaning reacts to its task's lifecycle, in the same transaction.

The task owns assignment and TODO/IN_PROGRESS/COMPLETED; these hooks keep the
cleaning-specific state machine in step and enforce cleaning-only rules (a
cleaning can't be completed with checklist items left). Imported by
``tasks.service``; must not import it back.

Cleaning lifecycle::

    CREATED ⇄ ASSIGNED ──start──► IN_PROGRESS ──complete──► COMPLETED
                                       ▲                       │ worker (task.completed)
                                       │              ┌────────┴─────────┐
                              start (re-clean)   INSPECTION_REQUIRED   APPROVED (no inspection)
                                       │              │approve   │fail
                                    FAILED ◄──────────┘──────────┘
                                                   APPROVED
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.context import TenantContext
from app.cleaning import checklist as checklists
from app.cleaning.models import Cleaning, CleaningStatus
from app.core.errors import DomainValidationError
from app.core.time import utc_now
from app.events.recorder import record_change
from app.events.types import AggregateType, DomainEvent, EventType
from app.tasks.models import Task

C = CleaningStatus

# Cleaning transitions driven by explicit actions (the worker moves COMPLETED on).
ALLOWED: dict[CleaningStatus, frozenset[CleaningStatus]] = {
    C.CREATED: frozenset({C.ASSIGNED, C.IN_PROGRESS}),
    C.ASSIGNED: frozenset({C.CREATED, C.IN_PROGRESS}),
    C.IN_PROGRESS: frozenset({C.COMPLETED}),
    C.COMPLETED: frozenset({C.INSPECTION_REQUIRED, C.APPROVED, C.FAILED}),
    C.INSPECTION_REQUIRED: frozenset({C.APPROVED, C.FAILED}),
    C.APPROVED: frozenset(),
    C.FAILED: frozenset({C.IN_PROGRESS}),
}


def cleaning_for_task(session: Session, task: Task) -> Cleaning | None:
    return session.scalars(
        select(Cleaning).where(
            Cleaning.task_id == task.id, Cleaning.organization_id == task.organization_id
        )
    ).first()


def record_cleaning_event(
    session: Session,
    ctx: TenantContext,
    cleaning: Cleaning,
    task: Task,
    event_type: EventType,
    *,
    extra: dict[str, Any] | None = None,
    audit: dict[str, Any] | None = None,
    key_suffix: str | None = None,
) -> None:
    session.flush()  # bump versions first: they make the key deterministic
    done, total = checklists.progress(cleaning.checklist)
    payload: dict[str, Any] = {
        "cleaning_id": cleaning.id,
        "task_id": task.id,
        "status": cleaning.status,
        "unit_id": task.unit_id,
        "property_id": task.property_id,
        "reservation_id": task.reservation_id,
        **(extra or {}),
    }
    record_change(
        session,
        ctx,
        DomainEvent(
            event_type=event_type,
            aggregate_type=AggregateType.CLEANING,
            aggregate_id=cleaning.id,
            # Cleaning + task versions: deterministic per state transition, even when
            # only the task row changed (e.g. reassigning a FAILED cleaning).
            idempotency_key=key_suffix
            or f"{event_type.value}:{cleaning.id}:v{cleaning.version}:t{task.version}",
            payload=payload,
        ),
        audit_metadata={
            "title": task.title,
            "task_id": task.id,
            "status": cleaning.status,
            "checklist": f"{done}/{total}",
            **(audit or {}),
        },
    )


def _move(cleaning: Cleaning, target: CleaningStatus) -> None:
    if target not in ALLOWED[cleaning.status]:
        raise DomainValidationError(
            f"Cleaning cannot move from {cleaning.status.value} to {target.value}."
        )
    cleaning.status = target


def attach_cleaning(
    session: Session, ctx: TenantContext, task: Task, audit: dict[str, Any] | None = None
) -> Cleaning:
    cleaning = Cleaning(
        organization_id=task.organization_id,
        task_id=task.id,
        status=C.ASSIGNED if task.assigned_to else C.CREATED,
        checklist=checklists.default_checklist(),
    )
    session.add(cleaning)
    session.flush()
    record_cleaning_event(
        session,
        ctx,
        cleaning,
        task,
        EventType.CLEANING_CREATED,
        key_suffix=f"cleaning.created:{cleaning.id}",
        audit=audit,
    )
    return cleaning


def on_assigned(session: Session, ctx: TenantContext, task: Task, audit: dict[str, Any]) -> None:
    cleaning = cleaning_for_task(session, task)
    if cleaning is None:
        return
    if cleaning.status in (C.CREATED, C.ASSIGNED):
        cleaning.status = C.ASSIGNED if task.assigned_to else C.CREATED
    record_cleaning_event(
        session,
        ctx,
        cleaning,
        task,
        EventType.CLEANING_ASSIGNED,
        extra={"assigned_to": task.assigned_to},
        audit=audit,
    )


def on_started(session: Session, ctx: TenantContext, task: Task) -> None:
    cleaning = cleaning_for_task(session, task)
    if cleaning is None:
        return
    restart = cleaning.status == C.FAILED
    _move(cleaning, C.IN_PROGRESS)
    cleaning.started_at = utc_now()
    record_cleaning_event(
        session,
        ctx,
        cleaning,
        task,
        EventType.CLEANING_STARTED,
        extra={"restart": restart},
        audit={"restart": restart} if restart else None,
    )


def before_completed(session: Session, task: Task) -> None:
    """Synchronous validation: reject the request instead of failing in the worker."""
    cleaning = cleaning_for_task(session, task)
    if cleaning is None:
        return
    if cleaning.status != C.IN_PROGRESS:
        raise DomainValidationError("Start the cleaning before completing it.")
    if not checklists.is_complete(cleaning.checklist):
        done, total = checklists.progress(cleaning.checklist)
        raise DomainValidationError(
            f"Finish the checklist before completing the cleaning ({done}/{total} done).",
            details={"checklist_done": done, "checklist_total": total},
        )


def on_completed(session: Session, ctx: TenantContext, task: Task) -> None:
    cleaning = cleaning_for_task(session, task)
    if cleaning is None:
        return
    _move(cleaning, C.COMPLETED)
    cleaning.completed_at = utc_now()
    record_cleaning_event(session, ctx, cleaning, task, EventType.CLEANING_COMPLETED)
