"""Guest portal HTTP surface.

* ``/guest/*`` — guest-facing, authenticated by ``X-Guest-Token`` (a portal link).
  These routes never accept a member JWT, and staff routes never accept a guest
  token: the two authentication paths don't overlap.
* ``/reservations/{id}/portal-link`` — staff manage the link for a stay.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, status

from app.ai import service as ai
from app.ai.schemas import GuestAssistantOut
from app.auth.context import TenantContext
from app.auth.dependencies import DbSession, require
from app.auth.permissions import Permission
from app.core.errors import AuthenticationError
from app.core.idempotency import IdempotencyKey, execute_create
from app.core.time import utc_now
from app.events.dispatcher import commit_and_dispatch
from app.guest_portal import service
from app.guest_portal.schemas import (
    GuestKnowledgeOut,
    GuestMessageCreate,
    GuestMessageOut,
    GuestRequestCreateIn,
    GuestRequestView,
    GuestStayOut,
    PortalLinkIssued,
    PortalLinkStatus,
)
from app.inbox.models import Message

guest_router = APIRouter(prefix="/guest", tags=["guest portal"])
links_router = APIRouter(prefix="/reservations", tags=["guest portal"])


def get_guest_session(
    db: DbSession,
    x_guest_token: Annotated[
        str | None, Header(alias="X-Guest-Token", min_length=20, max_length=200)
    ] = None,
) -> service.GuestSession:
    if not x_guest_token:
        raise AuthenticationError("Open the guest portal from the link your host sent you.")
    return service.resolve_session(db, x_guest_token)


Guest = Annotated[service.GuestSession, Depends(get_guest_session)]


@guest_router.get("/stay", response_model=GuestStayOut, summary="The guest's stay")
def stay(db: DbSession, gs: Guest) -> GuestStayOut:
    return service.stay_overview(db, gs, utc_now())


@guest_router.get("/knowledge", response_model=GuestKnowledgeOut, summary="House guide")
def knowledge(db: DbSession, gs: Guest) -> GuestKnowledgeOut:
    return service.stay_knowledge(db, gs, utc_now())


@guest_router.get("/messages", response_model=list[GuestMessageOut])
def messages(db: DbSession, gs: Guest) -> list[GuestMessageOut]:
    return service.stay_messages(db, gs)


@guest_router.post("/messages", response_model=GuestMessageOut, status_code=status.HTTP_201_CREATED)
def send_message(
    db: DbSession, gs: Guest, body: GuestMessageCreate, idempotency_key: IdempotencyKey = None
) -> GuestMessageOut:
    message_id = execute_create(
        db,
        lambda: service.send_guest_message(db, gs, body.content, idempotency_key),
        lambda mid: mid,
    )
    out = service.get_message(db, gs, message_id)
    # The assistant (when enabled) answers after the message is safely stored.
    run_id = ai.start_guest_run(db, gs.ctx, db.get_one(Message, message_id))
    if run_id is not None:
        ai.submit(db, run_id)
    return out


@guest_router.get(
    "/assistant", response_model=GuestAssistantOut, summary="Assistant status and live progress"
)
def assistant(db: DbSession, gs: Guest) -> GuestAssistantOut:
    return service.assistant_state(db, gs)


@guest_router.post("/messages/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_read(db: DbSession, gs: Guest) -> None:
    service.mark_guest_read(db, gs)
    commit_and_dispatch(db)


@guest_router.get("/requests", response_model=list[GuestRequestView])
def list_requests(db: DbSession, gs: Guest) -> list[GuestRequestView]:
    return service.stay_requests(db, gs)


@guest_router.post(
    "/requests", response_model=GuestRequestView, status_code=status.HTTP_201_CREATED
)
def create_request(
    db: DbSession, gs: Guest, body: GuestRequestCreateIn, idempotency_key: IdempotencyKey = None
) -> GuestRequestView:
    request_id = execute_create(
        db,
        lambda: service.create_guest_request(
            db, gs, body.type, body.description, body.requested_for, idempotency_key
        ),
        lambda rid: rid,
    )
    return service.get_request(db, gs, request_id)


@guest_router.post("/requests/{request_id}/cancel", response_model=GuestRequestView)
def cancel_request(db: DbSession, gs: Guest, request_id: uuid.UUID) -> GuestRequestView:
    service.cancel_guest_request(db, gs, request_id)
    commit_and_dispatch(db)
    return service.get_request(db, gs, request_id)


# ------------------------------------------------------------------ staff: links

ReadCtx = Annotated[TenantContext, Depends(require(Permission.INBOX_READ))]
WriteCtx = Annotated[TenantContext, Depends(require(Permission.INBOX_WRITE))]


@links_router.get("/{reservation_id}/portal-link", response_model=PortalLinkStatus)
def portal_link_status(db: DbSession, ctx: ReadCtx, reservation_id: uuid.UUID) -> PortalLinkStatus:
    return service.link_status(db, ctx, reservation_id)


@links_router.post(
    "/{reservation_id}/portal-link",
    response_model=PortalLinkIssued,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a guest portal link (revokes the previous one)",
)
def issue_portal_link(db: DbSession, ctx: WriteCtx, reservation_id: uuid.UUID) -> PortalLinkIssued:
    issued = service.issue_link(db, ctx, reservation_id)
    commit_and_dispatch(db)
    return issued


@links_router.post("/{reservation_id}/portal-link/revoke", response_model=PortalLinkStatus)
def revoke_portal_link(db: DbSession, ctx: WriteCtx, reservation_id: uuid.UUID) -> PortalLinkStatus:
    service.revoke_link(db, ctx, reservation_id)
    commit_and_dispatch(db)
    return service.link_status(db, ctx, reservation_id)
