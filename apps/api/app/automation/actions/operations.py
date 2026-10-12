"""Operations actions: tasks, work orders, alerts — through the domain services."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Literal

from pydantic import Field, model_validator
from sqlalchemy import select

from app.alerts import service as alerts
from app.alerts.models import AlertSeverity
from app.auth.permissions import Permission
from app.automation.actions.base import (
    ActionDef,
    ActionEnv,
    ActionInput,
    ActionResult,
    Plan,
    register,
)
from app.core.errors import DomainValidationError, NotFoundError
from app.guest_requests import service as guest_requests
from app.guest_requests.models import GuestRequest
from app.guest_requests.schemas import ConvertToMaintenance
from app.maintenance import service as maintenance
from app.maintenance.models import ACTIVE_MAINTENANCE_STATUSES, MaintenanceCategory, WorkOrder
from app.maintenance.schemas import WorkOrderCreate
from app.operations.turnover import next_arrival, turnover_deadline, turnover_window
from app.properties.models import Property, Unit
from app.reservations.models import Reservation, ReservationStatus
from app.tasks import service as tasks
from app.tasks.models import OPEN_TASK_STATUSES, PRIORITY_RANK, Task, TaskPriority, TaskType
from app.tasks.schemas import TaskUpdate

TaskTarget = Literal["subject_task", "turnover_task"]


# ----------------------------------------------------------------- helpers


def _property(env: ActionEnv, property_id: uuid.UUID | None) -> Property:
    prop = (
        env.session.scalars(
            select(Property).where(
                Property.id == property_id, Property.organization_id == env.ctx.organization_id
            )
        ).first()
        if property_id
        else None
    )
    if prop is None:
        raise NotFoundError("The property for this action no longer exists.")
    return prop


def _unit(env: ActionEnv, unit_id: uuid.UUID | None) -> Unit | None:
    if unit_id is None:
        return None
    unit = env.session.scalars(
        select(Unit).where(Unit.id == unit_id, Unit.organization_id == env.ctx.organization_id)
    ).first()
    if unit is None:
        raise NotFoundError("The unit for this action no longer exists.")
    return unit


def _reservation(env: ActionEnv) -> Reservation | None:
    rid = env.ref("reservation")
    if rid is None:
        return None
    return env.session.scalars(
        select(Reservation).where(
            Reservation.id == rid, Reservation.organization_id == env.ctx.organization_id
        )
    ).first()


def _member_label(env: ActionEnv, user_id: uuid.UUID) -> str:
    return tasks.member_name(tasks.assert_assignable(env.session, env.ctx, user_id))


def _target_task(env: ActionEnv, target: TaskTarget) -> Task:
    if target == "subject_task":
        task_id = env.ref("task")
        if task_id is None:
            raise DomainValidationError("This workflow's trigger has no task to act on.")
        task = env.session.scalars(
            select(Task).where(Task.id == task_id, Task.organization_id == env.ctx.organization_id)
        ).first()
    else:
        rid = env.ref("reservation")
        if rid is None:
            raise DomainValidationError("This workflow's trigger has no reservation.")
        task = env.session.scalars(
            select(Task).where(
                Task.organization_id == env.ctx.organization_id,
                Task.source_key == f"turnover:{rid}",
            )
        ).first()
    if task is None:
        raise NotFoundError("The task to act on doesn't exist (yet).")
    return task


def _task_snapshot(task: Task) -> dict[str, object]:
    return {
        "task_status": task.status.value,
        "task_version": task.version,
        "assigned_to": str(task.assigned_to) if task.assigned_to else None,
        "priority": task.priority.value,
    }


def _require_open(task: Task) -> None:
    if task.status not in OPEN_TASK_STATUSES:
        raise DomainValidationError(
            f"The task “{task.title}” is {task.status.value.lower()} — nothing to change."
        )


def _task_refs(inputs: TaskTargetInput) -> set[str]:
    return {"task"} if inputs.target == "subject_task" else {"reservation"}


# --------------------------------------------------------------- create_task


class CreateTaskInput(ActionInput):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    task_type: Literal["OTHER", "INSPECTION", "MAINTENANCE", "TURNOVER_CLEANING"] = "OTHER"
    priority: TaskPriority = TaskPriority.NORMAL
    # subject: at the trigger's unit (or property); property: at property_id.
    location: Literal["subject", "property"] = "subject"
    property_id: uuid.UUID | None = None
    assign_to: uuid.UUID | None = None
    due_in_minutes: int | None = Field(default=None, ge=0, le=20160)

    @model_validator(mode="after")
    def _check(self) -> CreateTaskInput:
        if self.location == "property" and self.property_id is None:
            raise ValueError("choose a property")
        if self.task_type == "TURNOVER_CLEANING" and self.location != "subject":
            raise ValueError("a turnover cleaning is created for the trigger's reservation")
        return self


def _create_task_refs(inputs: CreateTaskInput) -> set[str]:
    if inputs.task_type == "TURNOVER_CLEANING":
        return {"reservation"}
    return {"property"} if inputs.location == "subject" else set()


def _plan_create_task(env: ActionEnv, inputs: CreateTaskInput) -> Plan:
    unit = None
    reservation = None
    if inputs.task_type == "TURNOVER_CLEANING":
        reservation = _reservation(env)
        if reservation is None:
            raise NotFoundError("The reservation no longer exists.")
        if reservation.status == ReservationStatus.CANCELLED:
            raise DomainValidationError("The reservation was cancelled; no turnover is needed.")
        prop = _property(env, reservation.property_id)
        unit = _unit(env, reservation.unit_id)
        existing = env.session.scalar(
            select(Task.id).where(
                Task.organization_id == env.ctx.organization_id,
                Task.source_key == f"turnover:{reservation.id}",
            )
        )
    else:
        if inputs.location == "property":
            prop = _property(env, inputs.property_id)
        else:
            prop = _property(env, env.ref("property"))
            unit = _unit(env, env.ref("unit"))
            reservation = _reservation(env)
        existing = None
    assignee = _member_label(env, inputs.assign_to) if inputs.assign_to else None
    where = f"Unit {unit.name}" if unit else prop.name
    params = inputs.model_dump(mode="json")
    params |= {
        "property_id": str(prop.id),
        "unit_id": str(unit.id) if unit else None,
        "reservation_id": str(reservation.id) if reservation else None,
    }
    if existing is not None:
        return Plan(
            summary=f"Turnover cleaning for {where} already exists — nothing to create.",
            params=params | {"existing_task_id": str(existing)},
            entity_type="task",
            entity_id=existing,
            entity_label=where,
            expected_effect="No change (StayOps already created this turnover).",
        )
    return Plan(
        summary=f"Create {inputs.priority.value.lower()} task “{inputs.title}” at {where}"
        + (f", assigned to {assignee}" if assignee else ""),
        params=params,
        entity_type="property" if unit is None else "unit",
        entity_id=unit.id if unit else prop.id,
        entity_label=where,
        expected_effect=(
            f"A new {inputs.task_type.replace('_', ' ').lower()} task appears on the Tasks board."
        ),
    )


def _execute_create_task(env: ActionEnv, inputs: CreateTaskInput) -> ActionResult:
    plan = _plan_create_task(env, inputs)
    params = plan.params
    if params.get("existing_task_id"):
        return ActionResult(
            summary=plan.summary,
            output={"task_id": params["existing_task_id"], "already_existed": True},
            entity_type="task",
            entity_id=uuid.UUID(params["existing_task_id"]),
            created=False,
        )
    property_id = uuid.UUID(params["property_id"])
    unit_id = uuid.UUID(params["unit_id"]) if params["unit_id"] else None
    reservation_id = uuid.UUID(params["reservation_id"]) if params["reservation_id"] else None
    due = env.now + timedelta(minutes=inputs.due_in_minutes) if inputs.due_in_minutes else None
    task_type = TaskType(inputs.task_type)
    source_key = f"automation:{env.key}"
    if task_type == TaskType.TURNOVER_CLEANING:
        # Same key as the built-in checkout handler: StayOps never creates two
        # turnover cleanings for one stay, whoever gets there first.
        assert reservation_id is not None and unit_id is not None  # noqa: S101
        source_key = f"turnover:{reservation_id}"
        reservation = env.session.get_one(Reservation, reservation_id)
        prop = env.session.get_one(Property, property_id)
        arrival = next_arrival(
            env.session, env.ctx.organization_id, unit_id, reservation.check_out, reservation.id
        )
        window = turnover_window(
            reservation.check_out, arrival.check_in if arrival else None, prop.timezone
        )
        due = due or turnover_deadline(
            reservation.check_out,
            arrival.check_in if arrival else None,
            prop.timezone,
            prop.default_check_in_time,
        )
    task = tasks.create_automated_task(
        env.session,
        env.ctx,
        source_key=source_key,
        task_type=task_type,
        title=inputs.title,
        description=inputs.description,
        property_id=property_id,
        unit_id=unit_id,
        reservation_id=reservation_id,
        priority=inputs.priority,
        due_at=due,
        payload={"workflow": env.workflow_name}
        | (
            {"turnover_minutes": window.minutes, "tight_turnover": window.tight}
            if task_type == TaskType.TURNOVER_CLEANING
            else {}
        ),
    )
    if task is None:  # created by someone else first (or by this step before a retry)
        existing = env.session.scalars(
            select(Task).where(
                Task.organization_id == env.ctx.organization_id, Task.source_key == source_key
            )
        ).one()
        return ActionResult(
            summary=f"Task “{existing.title}” already existed — not created again.",
            output={"task_id": str(existing.id), "already_existed": True},
            entity_type="task",
            entity_id=existing.id,
            created=False,
        )
    if inputs.assign_to:
        tasks.assign_task(env.session, env.ctx, task.id, inputs.assign_to)
    return ActionResult(
        summary=f"Created task “{task.title}”",
        output={"task_id": str(task.id), "title": task.title, "priority": task.priority.value},
        entity_type="task",
        entity_id=task.id,
    )


register(
    ActionDef(
        type="create_task",
        label="Create task",
        category="Operations",
        description="Create an operations task (a turnover cleaning is never duplicated).",
        input_model=CreateTaskInput,
        risk="LOW",
        permissions=(Permission.TASKS_MANAGE,),
        plan=_plan_create_task,
        execute=_execute_create_task,
        refs=_create_task_refs,
        template_fields=("title", "description"),
        member_fields=("assign_to",),
        property_fields=("property_id",),
    )
)


# --------------------------------------------------------------- assign_task


class TaskTargetInput(ActionInput):
    target: TaskTarget = "subject_task"


class AssignTaskInput(TaskTargetInput):
    assignee_id: uuid.UUID


def _plan_assign_task(env: ActionEnv, inputs: AssignTaskInput) -> Plan:
    task = _target_task(env, inputs.target)
    _require_open(task)
    assignee = _member_label(env, inputs.assignee_id)
    return Plan(
        summary=f"Assign “{task.title}” to {assignee}",
        params=inputs.model_dump(mode="json") | {"task_id": str(task.id)},
        entity_type="task",
        entity_id=task.id,
        entity_label=task.title,
        expected_effect=f"{assignee} becomes responsible for the task.",
        snapshot=_task_snapshot(task),
        risk="MODERATE",
    )


def _execute_assign_task(env: ActionEnv, inputs: AssignTaskInput) -> ActionResult:
    plan = _plan_assign_task(env, inputs)
    task_id = uuid.UUID(plan.params["task_id"])
    tasks.assign_task(env.session, env.ctx, task_id, inputs.assignee_id)
    return ActionResult(
        summary=plan.summary,
        output={"task_id": str(task_id), "assignee_id": str(inputs.assignee_id)},
        entity_type="task",
        entity_id=task_id,
    )


register(
    ActionDef(
        type="assign_task",
        label="Assign task",
        category="Operations",
        description="Assign (or reassign) the trigger's task, or the stay's turnover cleaning.",
        input_model=AssignTaskInput,
        risk="MODERATE",
        permissions=(Permission.TASKS_MANAGE,),
        plan=_plan_assign_task,
        execute=_execute_assign_task,
        refs=_task_refs,
        member_fields=("assignee_id",),
    )
)


# ------------------------------------------------------- update_task_priority


class TaskPriorityInput(TaskTargetInput):
    priority: TaskPriority
    # escalate_only never lowers a priority someone raised on purpose.
    mode: Literal["set", "escalate_only"] = "escalate_only"


def _plan_priority(env: ActionEnv, inputs: TaskPriorityInput) -> Plan:
    task = _target_task(env, inputs.target)
    _require_open(task)
    unchanged = task.priority == inputs.priority or (
        inputs.mode == "escalate_only"
        and PRIORITY_RANK[task.priority] >= PRIORITY_RANK[inputs.priority]
    )
    return Plan(
        summary=(
            f"Keep “{task.title}” at {task.priority.value.lower()} priority"
            if unchanged
            else f"Change “{task.title}” priority {task.priority.value.lower()} → "
            f"{inputs.priority.value.lower()}"
        ),
        params=inputs.model_dump(mode="json") | {"task_id": str(task.id), "unchanged": unchanged},
        entity_type="task",
        entity_id=task.id,
        entity_label=task.title,
        expected_effect="The task moves up the board." if not unchanged else "No change.",
        snapshot=_task_snapshot(task),
        risk="MODERATE",
    )


def _execute_priority(env: ActionEnv, inputs: TaskPriorityInput) -> ActionResult:
    plan = _plan_priority(env, inputs)
    task_id = uuid.UUID(plan.params["task_id"])
    if not plan.params["unchanged"]:
        tasks.update_task(env.session, env.ctx, task_id, TaskUpdate(priority=inputs.priority))
    return ActionResult(
        summary=plan.summary,
        output={"task_id": str(task_id), "priority": inputs.priority.value},
        entity_type="task",
        entity_id=task_id,
        created=not plan.params["unchanged"],
    )


register(
    ActionDef(
        type="update_task_priority",
        label="Change task priority",
        category="Operations",
        description="Raise (or set) the priority of the trigger's task or the stay's turnover.",
        input_model=TaskPriorityInput,
        risk="MODERATE",
        permissions=(Permission.TASKS_MANAGE,),
        plan=_plan_priority,
        execute=_execute_priority,
        refs=_task_refs,
    )
)


# ------------------------------------------------- create_maintenance_work_order


class CreateWorkOrderInput(ActionInput):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    category: MaintenanceCategory = MaintenanceCategory.OTHER
    priority: TaskPriority = TaskPriority.NORMAL
    blocks_unit: bool = False
    assign_to: uuid.UUID | None = None


def _plan_work_order(env: ActionEnv, inputs: CreateWorkOrderInput) -> Plan:
    request_id = env.ref("guest_request")
    unit = _unit(env, env.ref("unit"))
    if unit is None:
        raise DomainValidationError("This workflow's trigger has no unit for the work order.")
    linked = None
    if request_id is not None:
        req = env.session.scalars(
            select(GuestRequest).where(
                GuestRequest.id == request_id,
                GuestRequest.organization_id == env.ctx.organization_id,
            )
        ).first()
        if req is None:
            raise NotFoundError("The guest request no longer exists.")
        linked = req.work_order_id
    assignee = _member_label(env, inputs.assign_to) if inputs.assign_to else None
    params = inputs.model_dump(mode="json") | {
        "unit_id": str(unit.id),
        "guest_request_id": str(request_id) if request_id else None,
        "reservation_id": str(env.ref("reservation")) if env.ref("reservation") else None,
    }
    if linked is not None:
        return Plan(
            summary="The guest request already has a work order — nothing to create.",
            params=params | {"existing_work_order_id": str(linked)},
            entity_type="work_order",
            entity_id=linked,
            entity_label=f"Unit {unit.name}",
            expected_effect="No change.",
        )
    return Plan(
        summary=f"Create {inputs.priority.value.lower()} work order “{inputs.title}” "
        f"for Unit {unit.name}"
        + (" — blocks the unit" if inputs.blocks_unit else "")
        + (f", assigned to {assignee}" if assignee else ""),
        params=params,
        entity_type="unit",
        entity_id=unit.id,
        entity_label=f"Unit {unit.name}",
        expected_effect=(
            "The unit is taken out of availability until the work order is resolved."
            if inputs.blocks_unit
            else "A new work order appears in Maintenance."
        ),
        risk="MODERATE" if inputs.blocks_unit else "LOW",
    )


def _execute_work_order(env: ActionEnv, inputs: CreateWorkOrderInput) -> ActionResult:
    plan = _plan_work_order(env, inputs)
    params = plan.params
    if params.get("existing_work_order_id"):
        return ActionResult(
            summary=plan.summary,
            output={"work_order_id": params["existing_work_order_id"], "already_existed": True},
            entity_type="work_order",
            entity_id=uuid.UUID(params["existing_work_order_id"]),
            created=False,
        )
    if params["guest_request_id"]:
        # Link it to the request (status, guest notice and team note follow the
        # normal conversion path; one work order per request).
        order_id = guest_requests.convert_to_maintenance(
            env.session,
            env.ctx,
            uuid.UUID(params["guest_request_id"]),
            ConvertToMaintenance(
                title=inputs.title,
                category=inputs.category,
                priority=inputs.priority,
                blocks_unit=inputs.blocks_unit,
                assigned_to=inputs.assign_to,
            ),
        )
    else:
        order_id = maintenance.create_work_order(
            env.session,
            env.ctx,
            WorkOrderCreate(
                title=inputs.title,
                description=inputs.description,
                category=inputs.category,
                priority=inputs.priority,
                unit_id=uuid.UUID(params["unit_id"]),
                reservation_id=uuid.UUID(params["reservation_id"])
                if params["reservation_id"]
                else None,
                blocks_unit=inputs.blocks_unit,
                assigned_to=inputs.assign_to,
            ),
            client_key=env.key,
        )
    return ActionResult(
        summary=f"Created work order “{inputs.title}”"
        + (" (unit blocked)" if inputs.blocks_unit else ""),
        output={"work_order_id": str(order_id), "blocks_unit": inputs.blocks_unit},
        entity_type="work_order",
        entity_id=order_id,
    )


register(
    ActionDef(
        type="create_maintenance_work_order",
        label="Create work order",
        category="Operations",
        description=(
            "Report a maintenance issue on the trigger's unit "
            "(linked to the guest request when there is one)."
        ),
        input_model=CreateWorkOrderInput,
        risk="LOW",
        risk_for=lambda i: "MODERATE" if i.blocks_unit else "LOW",
        permissions=(Permission.MAINTENANCE_MANAGE, Permission.GUEST_REQUESTS_MANAGE),
        plan=_plan_work_order,
        execute=_execute_work_order,
        refs=lambda _i: {"unit"},
        template_fields=("title", "description"),
        member_fields=("assign_to",),
    )
)


# ---------------------------------------------- assign_maintenance_work_order


class AssignWorkOrderInput(ActionInput):
    assignee_id: uuid.UUID


def _subject_work_order(env: ActionEnv) -> WorkOrder:
    order_id = env.ref("work_order")
    order = (
        env.session.scalars(
            select(WorkOrder).where(
                WorkOrder.id == order_id, WorkOrder.organization_id == env.ctx.organization_id
            )
        ).first()
        if order_id
        else None
    )
    if order is None:
        raise NotFoundError("The work order no longer exists.")
    return order


def _plan_assign_wo(env: ActionEnv, inputs: AssignWorkOrderInput) -> Plan:
    order = _subject_work_order(env)
    if order.status not in ACTIVE_MAINTENANCE_STATUSES:
        raise DomainValidationError(f"The work order is {order.status.value.lower()}.")
    assignee = _member_label(env, inputs.assignee_id)
    return Plan(
        summary=f"Assign work order “{order.title}” to {assignee}",
        params=inputs.model_dump(mode="json") | {"work_order_id": str(order.id)},
        entity_type="work_order",
        entity_id=order.id,
        entity_label=order.title,
        expected_effect=f"{assignee} becomes responsible for the fix.",
        snapshot={
            "status": order.status.value,
            "version": order.version,
            "assigned_to": str(order.assigned_to) if order.assigned_to else None,
        },
        risk="MODERATE",
    )


def _execute_assign_wo(env: ActionEnv, inputs: AssignWorkOrderInput) -> ActionResult:
    plan = _plan_assign_wo(env, inputs)
    order_id = uuid.UUID(plan.params["work_order_id"])
    maintenance.assign_work_order(env.session, env.ctx, order_id, inputs.assignee_id)
    return ActionResult(
        summary=plan.summary,
        output={"work_order_id": str(order_id), "assignee_id": str(inputs.assignee_id)},
        entity_type="work_order",
        entity_id=order_id,
    )


register(
    ActionDef(
        type="assign_maintenance_work_order",
        label="Assign work order",
        category="Operations",
        description="Assign the trigger's work order to a team member.",
        input_model=AssignWorkOrderInput,
        risk="MODERATE",
        permissions=(Permission.MAINTENANCE_MANAGE,),
        plan=_plan_assign_wo,
        execute=_execute_assign_wo,
        refs=lambda _i: {"work_order"},
        member_fields=("assignee_id",),
    )
)


# --------------------------------------------------- create_operational_alert


class AlertInput(ActionInput):
    severity: AlertSeverity = AlertSeverity.WARNING
    title: str = Field(min_length=1, max_length=200)
    detail: str | None = Field(default=None, max_length=1000)


_ALERT_ENTITY = (
    "task",
    "work_order",
    "reservation",
    "guest_request",
    "conversation",
    "unit",
    "property",
)


def _alert_target(env: ActionEnv) -> tuple[str | None, uuid.UUID | None]:
    for name in _ALERT_ENTITY:
        if env.ref(name):
            return name, env.ref(name)
    return None, None


def _plan_alert(env: ActionEnv, inputs: AlertInput) -> Plan:
    entity_type, entity_id = _alert_target(env)
    return Plan(
        summary=f"Raise {inputs.severity.value.lower()} alert “{inputs.title}”",
        params=inputs.model_dump(mode="json"),
        entity_type=entity_type,
        entity_id=entity_id,
        expected_effect="The alert appears in the Command Center and the notification bell.",
    )


def _execute_alert(env: ActionEnv, inputs: AlertInput) -> ActionResult:
    entity_type, entity_id = _alert_target(env)
    label = env.fields.get("reservation.confirmation_code") or env.fields.get("task.title")
    alert_id, created = alerts.raise_alert(
        env.session,
        env.ctx,
        alerts.NewAlert(
            severity=inputs.severity,
            title=inputs.title,
            detail=inputs.detail,
            dedupe_key=f"automation:{env.key}",
            entity_type=entity_type,
            entity_id=entity_id,
            entity_label=str(label) if label else None,
            property_id=env.ref("property"),
            workflow_id=env.workflow_id,
            execution_id=env.execution_id,
        ),
    )
    return ActionResult(
        summary=f"Raised alert “{inputs.title}”",
        output={"alert_id": str(alert_id), "severity": inputs.severity.value},
        entity_type="alert",
        entity_id=alert_id,
        created=created,
    )


register(
    ActionDef(
        type="create_operational_alert",
        label="Raise operational alert",
        category="Operations",
        description=(
            "Notify the team: the alert shows in the Command Center and the bell "
            "until acknowledged."
        ),
        input_model=AlertInput,
        risk="LOW",
        permissions=(Permission.TASKS_MANAGE,),
        plan=_plan_alert,
        execute=_execute_alert,
        template_fields=("title", "detail"),
    )
)
