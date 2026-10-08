from __future__ import annotations

import re
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.context import Actor, CurrentUser, TenantContext
from app.core.errors import NotFoundError
from app.events.recorder import record_change
from app.events.types import AggregateType, DomainEvent, EventType
from app.organizations.models import Organization, OrganizationMember, Role
from app.organizations.schemas import OrganizationCreate
from app.realtime.topics import organization_topic


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:48] or "org"


def realtime_topic(organization_id: uuid.UUID) -> str:
    return organization_topic(organization_id)


def list_memberships(
    session: Session, user_id: uuid.UUID
) -> list[tuple[Organization, OrganizationMember]]:
    rows = session.execute(
        select(Organization, OrganizationMember)
        .join(OrganizationMember, OrganizationMember.organization_id == Organization.id)
        .where(OrganizationMember.user_id == user_id)
        .order_by(OrganizationMember.joined_at, Organization.name)
    ).all()
    return [(org, member) for org, member in rows]


def get_organization(session: Session, ctx: TenantContext) -> Organization:
    org = session.get(Organization, ctx.organization_id)
    if org is None:
        raise NotFoundError()
    return org


def create_organization(
    session: Session, user: CurrentUser, data: OrganizationCreate, *, source: str = "api"
) -> Organization:
    base = slugify(data.name)
    slug = base
    while session.scalar(select(Organization.id).where(Organization.slug == slug)) is not None:
        slug = f"{base}-{secrets.token_hex(3)}"

    org = Organization(name=data.name, slug=slug, default_currency=data.default_currency)
    session.add(org)
    session.flush()
    session.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=Role.OWNER))
    session.flush()

    ctx = TenantContext(
        organization_id=org.id, role=Role.OWNER, actor=Actor.user(user), source=source
    )
    record_change(
        session,
        ctx,
        DomainEvent(
            event_type=EventType.ORGANIZATION_CREATED,
            aggregate_type=AggregateType.ORGANIZATION,
            aggregate_id=org.id,
            idempotency_key=f"organization.created:{org.id}",
            payload={"name": org.name, "slug": org.slug, "owner_id": user.id},
        ),
        audit_metadata={"name": org.name, "slug": org.slug},
    )
    return org
