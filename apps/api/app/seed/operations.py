"""Demo operations: staff, turnover cleanings, tasks and maintenance work orders.

Runs after the demo stays exist. Turnover cleanings are *not* created here: they
were created by the real ``reservation.checked_out`` automation while the seed
checked guests out. This module then plays the team's day through the domain
services — cleaners act as STAFF on their own tasks, the owner inspects and
assigns — so every row has the events, audit entries and realtime notices an
operator action would produce.

Resulting picture (relative to "now" in each property's timezone):

* approved cleanings for past departures, one awaiting inspection
* a tight same-day turnover in progress (Harbour Lofts L1)
* an overdue turnover with a guest arriving today (Harbour Lofts L2)
* a failed inspection waiting for a re-clean (Canal View 202), during which the
  cleaner reported a broken AC → a unit-blocking work order (unit not bookable)
* open, on-hold, resolved and closed work orders; an urgent unassigned task
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.context import Actor, CurrentUser, TenantContext
from app.cleaning import service as cleanings
from app.cleaning.lifecycle import cleaning_for_task
from app.cleaning.models import Cleaning
from app.cleaning.schemas import ChecklistUpdate, CleaningUpdate
from app.core.time import local_to_utc
from app.events.dispatcher import commit_and_dispatch
from app.maintenance import service as maintenance
from app.maintenance.models import MaintenanceCategory, WorkOrder
from app.maintenance.schemas import WorkOrderCreate
from app.organizations.models import OrganizationMember, Profile, Role
from app.properties.models import Property, Unit
from app.reservations.models import Reservation
from app.tasks import service as tasks
from app.tasks.models import Task, TaskPriority, TaskType
from app.tasks.schemas import TaskCreate, TaskUpdate

# Stable ids: re-seeding reuses the same staff profiles (they are not org-scoped).
_STAFF_NAMESPACE = uuid.UUID("5d1f6a2e-3c1b-4e7a-9a52-0b8f6c2d7e41")


@dataclass(frozen=True)
class StaffSpec:
    key: str
    full_name: str
    email: str


STAFF = (
    StaffSpec("maria", "Maria Santos", "maria.santos@canal-stays.example"),
    StaffSpec("tom", "Tom Becker", "tom.becker@canal-stays.example"),
    StaffSpec("raj", "Raj Patel", "raj.patel@canal-stays.example"),
)


def staff_id(key: str) -> uuid.UUID:
    return uuid.uuid5(_STAFF_NAMESPACE, f"stayops-demo-staff:{key}")


def _add_staff(session: Session, organization_id: uuid.UUID) -> dict[str, CurrentUser]:
    """Team members who receive work. They exist as profiles + STAFF memberships;
    demo staff have no login (assignment and workload only)."""
    users: dict[str, CurrentUser] = {}
    for spec in STAFF:
        uid = staff_id(spec.key)
        profile = session.get(Profile, uid)
        if profile is None:
            session.add(Profile(id=uid, email=spec.email, full_name=spec.full_name))
        else:
            profile.email, profile.full_name = spec.email, spec.full_name
        session.flush()
        session.add(
            OrganizationMember(organization_id=organization_id, user_id=uid, role=Role.STAFF)
        )
        users[spec.key] = CurrentUser(id=uid, email=spec.email, full_name=spec.full_name)
    session.commit()
    return users


class _Ops:
    def __init__(
        self,
        session: Session,
        manager: TenantContext,
        staff: dict[str, CurrentUser],
        now: datetime,
    ) -> None:
        self.session = session
        self.manager = manager
        self.staff = staff
        self.now = now

    # ---------------------------------------------------------------- lookups

    def as_staff(self, key: str) -> TenantContext:
        return TenantContext(
            organization_id=self.manager.organization_id,
            role=Role.STAFF,
            actor=Actor.user(self.staff[key]),
            source="seed",
        )

    def reservation(self, code: str) -> Reservation:
        return self.session.scalars(
            select(Reservation).where(
                Reservation.organization_id == self.manager.organization_id,
                Reservation.confirmation_code == code,
            )
        ).one()

    def turnover(self, code: str) -> tuple[Task, Cleaning]:
        """The turnover cleaning the checkout automation created for this stay."""
        reservation = self.reservation(code)
        task = self.session.scalars(
            select(Task).where(
                Task.organization_id == self.manager.organization_id,
                Task.source_key == f"turnover:{reservation.id}",
            )
        ).one()
        cleaning = cleaning_for_task(self.session, task)
        assert cleaning is not None  # noqa: S101 — turnover tasks always carry a cleaning
        return task, cleaning

    def unit(self, prop: str, name: str) -> Unit:
        return self.session.scalars(
            select(Unit)
            .join(Property, Property.id == Unit.property_id)
            .where(
                Unit.organization_id == self.manager.organization_id,
                Property.name == prop,
                Unit.name == name,
            )
        ).one()

    def local(self, prop: str, days: int, at: time) -> datetime:
        tz = self.session.scalars(
            select(Property.timezone).where(
                Property.organization_id == self.manager.organization_id, Property.name == prop
            )
        ).one()
        today = self.now.astimezone(ZoneInfo(tz)).date()
        return local_to_utc(today + timedelta(days=days), at, tz)

    def commit(self) -> None:
        commit_and_dispatch(self.session)

    # ---------------------------------------------------------------- actions

    def assign(self, task: Task, key: str) -> None:
        tasks.assign_task(self.session, self.manager, task.id, self.staff[key].id)
        self.commit()

    def start(self, task: Task, key: str) -> None:
        tasks.start_task(self.session, self.as_staff(key), task.id)
        self.commit()

    def tick(self, cleaning: Cleaning, key: str, count: int | None = None) -> None:
        items = cleaning.checklist if count is None else cleaning.checklist[:count]
        cleanings.update_cleaning(
            self.session,
            self.as_staff(key),
            cleaning.id,
            CleaningUpdate(
                checklist=[ChecklistUpdate(id=item["id"], completed=True) for item in items]
            ),
        )
        self.commit()

    def finish(self, task: Task, key: str) -> None:
        """Completing a cleaning → the worker requests inspection (inline in the seed)."""
        tasks.complete_task(self.session, self.as_staff(key), task.id)
        self.commit()

    def clean(self, code: str, key: str) -> tuple[Task, Cleaning]:
        task, cleaning = self.turnover(code)
        self.assign(task, key)
        self.start(task, key)
        self.tick(cleaning, key)
        self.finish(task, key)
        return task, cleaning

    def approve(self, cleaning: Cleaning, notes: str | None = None) -> None:
        cleanings.approve(self.session, self.manager, cleaning.id, notes)
        self.commit()

    def work_order(self, ctx: TenantContext | None = None, **body: object) -> uuid.UUID:
        order_id = maintenance.create_work_order(
            self.session, ctx or self.manager, WorkOrderCreate.model_validate(body)
        )
        self.commit()
        return order_id

    def task(self, **body: object) -> uuid.UUID:
        task_id = tasks.create_task(self.session, self.manager, TaskCreate.model_validate(body))
        self.commit()
        return task_id


def seed_operations(session: Session, manager: TenantContext, now: datetime) -> dict[str, int]:
    staff = _add_staff(session, manager.organization_id)
    ops = _Ops(session, manager, staff, now)
    raj = staff["raj"].id

    # --- Past departures: cleaned and approved ---------------------------------------
    for code, cleaner in (
        ("CVA-1008", "maria"),
        ("CVA-1009", "tom"),
        ("HBL-2005", "maria"),
        ("BKB-3005", "tom"),
        ("CVA-1014", "maria"),
        ("BKB-3008", "tom"),
    ):
        _, cleaning = ops.clean(code, cleaner)
        ops.approve(cleaning, "Looks great.")

    # Cleaned, waiting for the inspector.
    ops.clean("HBL-2006", "tom")

    # --- Canal View 202: AC broken (found during cleaning) + failed inspection --------
    task, cleaning = ops.turnover("CVA-1006")
    ops.assign(task, "tom")
    ops.start(task, "tom")
    ops.tick(cleaning, "tom")
    ac = ops.work_order(
        ops.as_staff("tom"),
        title="AC not working",
        description="Unit blows warm air on every setting; thermostat display is blank.",
        category=MaintenanceCategory.HVAC,
        priority=TaskPriority.HIGH,
        source_task_id=str(task.id),
        blocks_unit=True,
    )
    ops.finish(task, "tom")
    cleanings.fail(session, manager, cleaning.id, "Bathroom mirror streaky and bins not emptied.")
    ops.commit()
    maintenance.assign_work_order(session, manager, ac, raj)
    ops.commit()
    maintenance.start_work_order(session, ops.as_staff("raj"), ac)
    ops.commit()

    # --- Harbour Lofts L1: tight same-day turnover, cleaner on it now -----------------
    task, cleaning = ops.turnover("HBL-2001")
    ops.assign(task, "maria")
    ops.start(task, "maria")
    ops.tick(cleaning, "maria", count=3)

    # --- Harbour Lofts L2: should have been cleaned yesterday afternoon ---------------
    task, _ = ops.turnover("HBL-2010")
    ops.assign(task, "maria")
    tasks.update_task(
        session,
        manager,
        task.id,
        TaskUpdate(due_at=ops.local("Harbour Lofts", -1, time(16, 0))),
    )
    ops.commit()

    # --- Other work -------------------------------------------------------------------
    arrival = ops.reservation("BKB-3003")
    ops.task(
        task_type=TaskType.GUEST_REQUEST,
        title="Set up travel cot before arrival",
        description="Guest asked for a travel cot and high chair for their toddler.",
        priority=TaskPriority.URGENT,
        reservation_id=str(arrival.id),
        due_at=(arrival.check_in - timedelta(hours=1)).isoformat(),
    )
    ops.task(
        task_type=TaskType.INSPECTION,
        title="Quarterly smoke alarm check",
        priority=TaskPriority.LOW,
        property_id=str(ops.unit("Canal View Apartments", "101").property_id),
        assigned_to=str(raj),
        due_at=ops.local("Canal View Apartments", 3, time(10, 0)).isoformat(),
    )
    ops.task(
        task_type=TaskType.OTHER,
        title="Restock welcome baskets",
        priority=TaskPriority.NORMAL,
        property_id=str(ops.unit("Harbour Lofts", "L1").property_id),
        assigned_to=str(staff["tom"].id),
        due_at=ops.local("Harbour Lofts", 1, time(12, 0)).isoformat(),
    )
    towels = ops.task(
        task_type=TaskType.GUEST_REQUEST,
        title="Extra towels for Unit 102",
        priority=TaskPriority.NORMAL,
        reservation_id=str(ops.reservation("CVA-1003").id),
        assigned_to=str(staff["maria"].id),
        due_at=ops.local("Canal View Apartments", 0, time(12, 0)).isoformat(),
    )
    tasks.complete_task(session, manager, towels, reason="Delivered at reception")
    ops.commit()

    # --- Maintenance ------------------------------------------------------------------
    ops.work_order(
        title="Dishwasher leaking",
        description="Small puddle under the dishwasher after each cycle.",
        category=MaintenanceCategory.APPLIANCE,
        unit_id=str(ops.unit("Canal View Apartments", "201").id),
    )
    ops.work_order(
        title="Water stain spreading on bedroom ceiling",
        description="Guest reported a damp patch above the bed; possible leak from upstairs.",
        category=MaintenanceCategory.PLUMBING,
        priority=TaskPriority.URGENT,
        unit_id=str(ops.unit("Brooklyn Brownstone", "Garden").id),
    )
    oven = ops.work_order(
        title="Replace oven heating element",
        category=MaintenanceCategory.APPLIANCE,
        unit_id=str(ops.unit("Brooklyn Brownstone", "Parlor").id),
        assigned_to=str(raj),
    )
    maintenance.start_work_order(session, ops.as_staff("raj"), oven)
    ops.commit()
    maintenance.block_work_order(
        session, ops.as_staff("raj"), oven, "Waiting for the replacement part (on order)."
    )
    ops.commit()
    fan = ops.work_order(
        title="Bathroom extractor fan noisy",
        category=MaintenanceCategory.ELECTRICAL,
        priority=TaskPriority.LOW,
        unit_id=str(ops.unit("Harbour Lofts", "L2").id),
        assigned_to=str(raj),
    )
    maintenance.start_work_order(session, ops.as_staff("raj"), fan)
    ops.commit()
    maintenance.resolve_work_order(session, ops.as_staff("raj"), fan, "Replaced the fan bearing.")
    ops.commit()
    lock = ops.work_order(
        title="Front door smart lock offline",
        category=MaintenanceCategory.LOCK,
        priority=TaskPriority.URGENT,
        unit_id=str(ops.unit("Harbour Lofts", "L1").id),
        blocks_unit=True,
        assigned_to=str(raj),
    )
    maintenance.start_work_order(session, ops.as_staff("raj"), lock)
    ops.commit()
    maintenance.resolve_work_order(
        session, ops.as_staff("raj"), lock, "Replaced batteries and re-paired the hub."
    )
    ops.commit()
    maintenance.close_work_order(session, manager, lock)
    ops.commit()

    org = manager.organization_id
    return {
        "staff": len(staff),
        "tasks": len(session.scalars(select(Task.id).where(Task.organization_id == org)).all()),
        "work_orders": len(
            session.scalars(select(WorkOrder.id).where(WorkOrder.organization_id == org)).all()
        ),
    }
