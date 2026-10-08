from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.auth.context import TenantContext
from app.auth.dependencies import DbSession, require
from app.auth.permissions import Permission
from app.channels.registry import all_adapters
from app.core.idempotency import IdempotencyKey, execute_create
from app.core.pagination import Page, PageParams, page_params
from app.events.dispatcher import commit_and_dispatch
from app.inbox import service
from app.inbox.context import ConversationContext, build_context
from app.inbox.models import Channel
from app.inbox.schemas import (
    ChannelOut,
    ConversationAssign,
    ConversationCreate,
    ConversationOut,
    ConversationUpdate,
    InboxCounts,
    MessageCreate,
    MessageOut,
)

router = APIRouter(prefix="/inbox", tags=["inbox"])

ReadCtx = Annotated[TenantContext, Depends(require(Permission.INBOX_READ))]
WriteCtx = Annotated[TenantContext, Depends(require(Permission.INBOX_WRITE))]

ViewParam = Annotated[
    str, Query(pattern="^(all|unread|open|mine|high|resolved)$", description="Inbox view")
]


def _filters(
    view: ViewParam = "all",
    q: Annotated[str | None, Query(max_length=100)] = None,
    guest_id: uuid.UUID | None = None,
    reservation_id: uuid.UUID | None = None,
    channel: Channel | None = None,
) -> service.InboxFilters:
    return service.InboxFilters(
        view=view, q=q, guest_id=guest_id, reservation_id=reservation_id, channel=channel
    )


Filters = Annotated[service.InboxFilters, Depends(_filters)]


def _get(db: DbSession, ctx: TenantContext, conversation_id: uuid.UUID) -> ConversationOut:
    return service.to_out(service.get_view(db, ctx, conversation_id))


@router.get("/channels", response_model=list[ChannelOut], summary="Messaging channels")
def list_channels(_: ReadCtx) -> list[ChannelOut]:
    return [
        ChannelOut(channel=a.channel, label=a.label, status=a.status.value) for a in all_adapters()
    ]


@router.get("/conversations", response_model=Page[ConversationOut], summary="Unified inbox")
def list_conversations(
    db: DbSession,
    ctx: ReadCtx,
    page: Annotated[PageParams, Depends(page_params)],
    filters: Filters,
) -> Page[ConversationOut]:
    views, total = service.list_conversations(db, ctx, page, filters)
    return Page(
        items=[service.to_out(v) for v in views], total=total, limit=page.limit, offset=page.offset
    )


@router.get("/counts", response_model=InboxCounts, summary="Conversation counts per view")
def inbox_counts(db: DbSession, ctx: ReadCtx, filters: Filters) -> InboxCounts:
    return service.inbox_counts(db, ctx, filters)


@router.post("/conversations", response_model=ConversationOut)
def create_conversation(
    db: DbSession, ctx: WriteCtx, body: ConversationCreate, response: Response
) -> ConversationOut:
    """Start (or reopen the existing) conversation with a guest about a stay."""
    conv, created = service.create_conversation(db, ctx, body)
    commit_and_dispatch(db)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return _get(db, ctx, conv.id)


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
def get_conversation(db: DbSession, ctx: ReadCtx, conversation_id: uuid.UUID) -> ConversationOut:
    return _get(db, ctx, conversation_id)


@router.get("/conversations/{conversation_id}/context", response_model=ConversationContext)
def conversation_context(
    db: DbSession, ctx: ReadCtx, conversation_id: uuid.UUID
) -> ConversationContext:
    """Guest, stay, property, unit, requests and linked work for the side panel."""
    return build_context(db, ctx, conversation_id)


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
def update_conversation(
    db: DbSession, ctx: WriteCtx, conversation_id: uuid.UUID, body: ConversationUpdate
) -> ConversationOut:
    service.update_conversation(db, ctx, conversation_id, body)
    commit_and_dispatch(db)
    return _get(db, ctx, conversation_id)


@router.post("/conversations/{conversation_id}/assign", response_model=ConversationOut)
def assign_conversation(
    db: DbSession, ctx: WriteCtx, conversation_id: uuid.UUID, body: ConversationAssign
) -> ConversationOut:
    service.assign_conversation(db, ctx, conversation_id, body.assignee_id, body.expected_version)
    commit_and_dispatch(db)
    return _get(db, ctx, conversation_id)


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
def list_messages(
    db: DbSession,
    ctx: ReadCtx,
    conversation_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    before_seq: Annotated[int | None, Query(ge=1)] = None,
) -> list[MessageOut]:
    return [
        service.message_out(m)
        for m in service.list_messages(db, ctx, conversation_id, limit=limit, before_seq=before_seq)
    ]


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
def send_message(
    db: DbSession,
    ctx: WriteCtx,
    conversation_id: uuid.UUID,
    body: MessageCreate,
    idempotency_key: IdempotencyKey = None,
) -> MessageOut:
    message_id = execute_create(
        db,
        lambda: service.send_message(
            db,
            ctx,
            conversation_id,
            body.content,
            internal=body.internal,
            client_key=idempotency_key,
        ),
        lambda mid: mid,
    )
    return service.message_out(service.get_message(db, ctx, message_id))


@router.post("/conversations/{conversation_id}/read", response_model=ConversationOut)
def mark_read(db: DbSession, ctx: WriteCtx, conversation_id: uuid.UUID) -> ConversationOut:
    service.mark_read(db, ctx, conversation_id, service.TEAM_SIDE)
    commit_and_dispatch(db)
    return _get(db, ctx, conversation_id)
