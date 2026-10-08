from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.auth.context import TenantContext
from app.auth.dependencies import DbSession, require
from app.auth.permissions import Permission
from app.events.dispatcher import commit_and_dispatch
from app.knowledge import service
from app.knowledge.schemas import KnowledgeCreate, KnowledgeOut, KnowledgeUpdate

router = APIRouter(tags=["property knowledge"])

ReadCtx = Annotated[TenantContext, Depends(require(Permission.PROPERTIES_READ))]
WriteCtx = Annotated[TenantContext, Depends(require(Permission.KNOWLEDGE_WRITE))]


@router.get("/properties/{property_id}/knowledge", response_model=list[KnowledgeOut])
def list_knowledge(db: DbSession, ctx: ReadCtx, property_id: uuid.UUID) -> list[KnowledgeOut]:
    return service.list_entries(db, ctx, property_id)


@router.post(
    "/properties/{property_id}/knowledge",
    response_model=KnowledgeOut,
    status_code=status.HTTP_201_CREATED,
)
def create_knowledge(
    db: DbSession, ctx: WriteCtx, property_id: uuid.UUID, body: KnowledgeCreate
) -> KnowledgeOut:
    entry_id = service.create_entry(db, ctx, property_id, body)
    commit_and_dispatch(db)
    return service.get_entry(db, ctx, entry_id)


@router.patch("/knowledge/{entry_id}", response_model=KnowledgeOut)
def update_knowledge(
    db: DbSession, ctx: WriteCtx, entry_id: uuid.UUID, body: KnowledgeUpdate
) -> KnowledgeOut:
    service.update_entry(db, ctx, entry_id, body)
    commit_and_dispatch(db)
    return service.get_entry(db, ctx, entry_id)


@router.post("/knowledge/{entry_id}/archive", status_code=status.HTTP_204_NO_CONTENT)
def archive_knowledge(db: DbSession, ctx: WriteCtx, entry_id: uuid.UUID) -> None:
    service.archive_entry(db, ctx, entry_id)
    commit_and_dispatch(db)
