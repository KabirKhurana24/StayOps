"""Property knowledge: structured guest information per property (and unit).

Reads are open to anyone who can read properties; writes need ``knowledge:write``
(OWNER/ADMIN/OPERATOR). Entries are archived, never deleted, so the audit trail
and anything an agent was told stay explainable.

``guest_entries`` is the *only* way guest-facing code (portal, guest agent) reads
knowledge: it applies visibility (no INTERNAL entries; STAY entries only from the
check-in day to the check-out day, in the property's timezone) and unit targeting.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.auth.context import TenantContext
from app.auth.permissions import Permission
from app.core.errors import ConflictError, DomainValidationError, NotFoundError
from app.core.time import utc_now
from app.events.recorder import diff, record_change
from app.events.types import AggregateType, DomainEvent, EventType
from app.knowledge.models import KnowledgeCategory, KnowledgeVisibility, PropertyKnowledge
from app.knowledge.schemas import KnowledgeCreate, KnowledgeOut, KnowledgeUpdate
from app.properties.models import Property, Unit
from app.reservations.models import Reservation

CATEGORY_ORDER = list(KnowledgeCategory)
_TRACKED = ("category", "title", "content", "visibility", "sort_order")


def _property(session: Session, ctx: TenantContext, property_id: uuid.UUID) -> Property:
    prop = session.scalars(
        select(Property).where(
            Property.id == property_id, Property.organization_id == ctx.organization_id
        )
    ).first()
    if prop is None:
        raise NotFoundError("Property not found.")
    return prop


def _load(session: Session, ctx: TenantContext, entry_id: uuid.UUID) -> PropertyKnowledge:
    entry = session.scalars(
        select(PropertyKnowledge).where(
            PropertyKnowledge.id == entry_id,
            PropertyKnowledge.organization_id == ctx.organization_id,
            PropertyKnowledge.archived_at.is_(None),
        )
    ).first()
    if entry is None:
        raise NotFoundError("Knowledge entry not found.")
    return entry


def _sort_key(entry: PropertyKnowledge) -> tuple[int, int, str]:
    return (CATEGORY_ORDER.index(entry.category), entry.sort_order, entry.title.lower())


def to_out(entry: PropertyKnowledge, unit_names: dict[uuid.UUID, str]) -> KnowledgeOut:
    return KnowledgeOut(
        id=entry.id,
        property_id=entry.property_id,
        unit_id=entry.unit_id,
        unit_name=unit_names.get(entry.unit_id) if entry.unit_id else None,
        category=entry.category,
        title=entry.title,
        content=entry.content,
        visibility=entry.visibility,
        sort_order=entry.sort_order,
        updated_by_label=entry.updated_by_label,
        version=entry.version,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


def list_entries(
    session: Session, ctx: TenantContext, property_id: uuid.UUID
) -> list[KnowledgeOut]:
    ctx.require(Permission.PROPERTIES_READ)
    prop = _property(session, ctx, property_id)
    entries = session.scalars(
        select(PropertyKnowledge).where(
            PropertyKnowledge.organization_id == ctx.organization_id,
            PropertyKnowledge.property_id == prop.id,
            PropertyKnowledge.archived_at.is_(None),
        )
    ).all()
    units = {
        uid: name
        for uid, name in session.execute(
            select(Unit.id, Unit.name).where(
                Unit.organization_id == ctx.organization_id, Unit.property_id == prop.id
            )
        )
    }
    return [to_out(e, units) for e in sorted(entries, key=_sort_key)]


def get_entry(session: Session, ctx: TenantContext, entry_id: uuid.UUID) -> KnowledgeOut:
    ctx.require(Permission.PROPERTIES_READ)
    entry = _load(session, ctx, entry_id)
    unit = session.get(Unit, entry.unit_id) if entry.unit_id else None
    return to_out(entry, {unit.id: unit.name} if unit else {})


def _record(
    session: Session,
    ctx: TenantContext,
    entry: PropertyKnowledge,
    event_type: EventType,
    audit: dict[str, Any] | None = None,
    key: str | None = None,
) -> None:
    prop = session.get_one(Property, entry.property_id)
    record_change(
        session,
        ctx,
        DomainEvent(
            event_type=event_type,
            aggregate_type=AggregateType.KNOWLEDGE,
            aggregate_id=entry.id,
            idempotency_key=key or f"{event_type.value}:{entry.id}:v{entry.version}",
            payload={
                "entry_id": entry.id,
                "property_id": entry.property_id,
                "unit_id": entry.unit_id,
                "category": entry.category,
                "visibility": entry.visibility,
                "version": entry.version,
            },
        ),
        audit_metadata={
            "property": prop.name,
            "category": entry.category,
            "title": entry.title,
            "visibility": entry.visibility,
            **(audit or {}),
        },
    )


def create_entry(
    session: Session, ctx: TenantContext, property_id: uuid.UUID, data: KnowledgeCreate
) -> uuid.UUID:
    ctx.require(Permission.KNOWLEDGE_WRITE)
    prop = _property(session, ctx, property_id)
    if data.unit_id is not None:
        unit = session.scalars(
            select(Unit).where(Unit.id == data.unit_id, Unit.organization_id == ctx.organization_id)
        ).first()
        if unit is None:
            raise NotFoundError("Unit not found.")
        if unit.property_id != prop.id:
            raise DomainValidationError(f"Unit {unit.name} does not belong to {prop.name}.")
    entry = PropertyKnowledge(
        organization_id=ctx.organization_id,
        property_id=prop.id,
        updated_by_label=ctx.actor.label,
        **data.model_dump(),
    )
    session.add(entry)
    session.flush()
    _record(session, ctx, entry, EventType.KNOWLEDGE_CREATED, key=f"knowledge.created:{entry.id}")
    return entry.id


def update_entry(
    session: Session, ctx: TenantContext, entry_id: uuid.UUID, data: KnowledgeUpdate
) -> uuid.UUID:
    ctx.require(Permission.KNOWLEDGE_WRITE)
    entry = _load(session, ctx, entry_id)
    if data.expected_version is not None and data.expected_version != entry.version:
        raise ConflictError(
            "This entry was changed by someone else. Reload and try again.",
            details={"current_version": entry.version},
        )
    changes_in = data.model_dump(exclude_unset=True, exclude={"expected_version"})
    before = {f: getattr(entry, f) for f in _TRACKED}
    changes = diff(before, {**before, **changes_in})
    if not changes:
        return entry.id
    for field, value in changes_in.items():
        setattr(entry, field, value)
    entry.updated_by_label = ctx.actor.label
    try:
        session.flush()
    except StaleDataError as exc:
        raise ConflictError(
            "This entry was changed by someone else. Reload and try again."
        ) from exc
    # Content can be long and may include codes: record which fields changed, and the
    # before/after only for the short descriptive ones.
    audit_changes = {
        k: (v if k in ("category", "title", "visibility", "sort_order") else "changed")
        for k, v in changes.items()
    }
    _record(session, ctx, entry, EventType.KNOWLEDGE_UPDATED, audit={"changes": audit_changes})
    return entry.id


def archive_entry(session: Session, ctx: TenantContext, entry_id: uuid.UUID) -> uuid.UUID:
    ctx.require(Permission.KNOWLEDGE_WRITE)
    entry = _load(session, ctx, entry_id)
    entry.archived_at = utc_now()
    session.flush()
    _record(session, ctx, entry, EventType.KNOWLEDGE_ARCHIVED)
    return entry.id


# ------------------------------------------------------------------ guest-facing


def in_stay_window(reservation: Reservation, timezone: str, now: datetime) -> bool:
    """From the check-in day (local) until the end of the check-out day (local)."""
    tz = ZoneInfo(timezone)
    today = now.astimezone(tz).date()
    return (
        reservation.check_in.astimezone(tz).date()
        <= today
        <= reservation.check_out.astimezone(tz).date()
    )


def guest_entries(
    session: Session,
    organization_id: uuid.UUID,
    reservation: Reservation,
    now: datetime,
    *,
    categories: list[KnowledgeCategory] | None = None,
) -> list[PropertyKnowledge]:
    """Knowledge a guest of ``reservation`` may see right now (authoritative source)."""
    prop = session.get_one(Property, reservation.property_id)
    allowed = [KnowledgeVisibility.PUBLIC]
    if in_stay_window(reservation, prop.timezone, now):
        allowed.append(KnowledgeVisibility.STAY)
    query = select(PropertyKnowledge).where(
        PropertyKnowledge.organization_id == organization_id,
        PropertyKnowledge.property_id == reservation.property_id,
        PropertyKnowledge.archived_at.is_(None),
        PropertyKnowledge.visibility.in_(allowed),
        or_(
            PropertyKnowledge.unit_id.is_(None),
            PropertyKnowledge.unit_id == reservation.unit_id,
        ),
    )
    if categories:
        query = query.where(PropertyKnowledge.category.in_(categories))
    return sorted(session.scalars(query).all(), key=_sort_key)


def stay_access_opens(reservation: Reservation, timezone: str) -> datetime:
    """Start of the check-in day in the property's timezone (when STAY entries unlock)."""
    tz = ZoneInfo(timezone)
    local = reservation.check_in.astimezone(tz)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)
