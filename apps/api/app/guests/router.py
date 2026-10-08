from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.auth.context import TenantContext
from app.auth.dependencies import DbSession, require
from app.auth.permissions import Permission
from app.core.idempotency import IdempotencyKey, execute_create
from app.core.pagination import Page, PageParams, page_params
from app.core.time import utc_now
from app.guests import service
from app.guests.models import Guest
from app.guests.schemas import GuestCreate, GuestOut, GuestStats
from app.guests.stats import guest_stats

router = APIRouter(prefix="/guests", tags=["guests"])

ReadCtx = Annotated[TenantContext, Depends(require(Permission.GUESTS_READ))]


def _out(guest: Guest, stats: GuestStats | None = None) -> GuestOut:
    return GuestOut.model_validate(guest).model_copy(update={"stats": stats})


@router.get("", response_model=Page[GuestOut])
def list_guests(
    db: DbSession,
    ctx: ReadCtx,
    page: Annotated[PageParams, Depends(page_params)],
    q: Annotated[str | None, Query(max_length=100)] = None,
    include_stats: bool = True,
) -> Page[GuestOut]:
    rows, total = service.list_guests(db, ctx, page, q=q)
    stats = guest_stats(db, ctx, [g.id for g in rows], utc_now()) if include_stats else {}
    return Page(
        items=[_out(g, stats.get(g.id)) for g in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("", response_model=GuestOut, status_code=status.HTTP_201_CREATED)
def create_guest(
    db: DbSession,
    ctx: Annotated[TenantContext, Depends(require(Permission.GUESTS_WRITE))],
    body: GuestCreate,
    idempotency_key: IdempotencyKey = None,
) -> GuestOut:
    guest = execute_create(
        db,
        lambda: service.create_guest(db, ctx, body, idempotency_key),
        lambda gid: service.get_guest(db, ctx, gid),
    )
    return _out(guest)


@router.get("/{guest_id}", response_model=GuestOut)
def get_guest(db: DbSession, ctx: ReadCtx, guest_id: uuid.UUID) -> GuestOut:
    guest = service.get_guest(db, ctx, guest_id)
    return _out(guest, guest_stats(db, ctx, [guest.id], utc_now()).get(guest.id))
