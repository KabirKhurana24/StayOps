"""Assignable team members and their live workload (no second staff table)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.context import TenantContext
from app.auth.permissions import ASSIGNABLE_ROLES
from app.maintenance.models import ACTIVE_MAINTENANCE_STATUSES, WorkOrder
from app.organizations.models import OrganizationMember, Profile
from app.tasks.models import OPEN_TASK_STATUSES, Task
from app.tasks.schemas import StaffMember
from app.tasks.service import member_name


def assignable_members(session: Session, ctx: TenantContext, now: datetime) -> list[StaffMember]:
    org = ctx.organization_id
    task_load = (
        select(
            Task.assigned_to.label("uid"),
            func.count().label("active"),
            func.count().filter(Task.due_at < now).label("overdue"),
        )
        .where(Task.organization_id == org, Task.status.in_(OPEN_TASK_STATUSES))
        .group_by(Task.assigned_to)
        .subquery()
    )
    order_load = (
        select(WorkOrder.assigned_to.label("uid"), func.count().label("active"))
        .where(
            WorkOrder.organization_id == org,
            WorkOrder.status.in_(ACTIVE_MAINTENANCE_STATUSES),
        )
        .group_by(WorkOrder.assigned_to)
        .subquery()
    )
    rows = session.execute(
        select(
            Profile,
            OrganizationMember.role,
            func.coalesce(task_load.c.active, 0),
            func.coalesce(task_load.c.overdue, 0),
            func.coalesce(order_load.c.active, 0),
        )
        .join(OrganizationMember, OrganizationMember.user_id == Profile.id)
        .outerjoin(task_load, task_load.c.uid == Profile.id)
        .outerjoin(order_load, order_load.c.uid == Profile.id)
        .where(
            OrganizationMember.organization_id == org,
            OrganizationMember.role.in_(ASSIGNABLE_ROLES),
        )
        .order_by(func.coalesce(Profile.full_name, Profile.email), Profile.id)
    ).all()
    return [
        StaffMember(
            user_id=p.id,
            name=member_name(p),
            email=p.email,
            role=role,
            active_tasks=int(active),
            overdue_tasks=int(overdue),
            active_work_orders=int(orders),
        )
        for p, role, active, overdue, orders in rows
    ]
