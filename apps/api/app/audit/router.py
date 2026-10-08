from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime

from app.audit.schemas import AuditLogOut
from app.audit.service import list_audit
from app.auth.context import TenantContext
from app.auth.dependencies import DbSession, require
from app.auth.permissions import Permission
from app.core.pagination import Page, PageParams, page_params

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=Page[AuditLogOut], summary="Audit timeline")
def get_audit_timeline(
    db: DbSession,
    ctx: Annotated[TenantContext, Depends(require(Permission.ACTIVITY_READ))],
    page: Annotated[PageParams, Depends(page_params)],
    entity_type: Annotated[list[str] | None, Query()] = None,
    entity_id: uuid.UUID | None = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    since: AwareDatetime | None = None,
    until: AwareDatetime | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[AuditLogOut]:
    rows, total = list_audit(
        db,
        ctx,
        page,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        since=since,
        until=until,
        q=q,
    )
    return Page(
        items=[AuditLogOut.model_validate(r) for r in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
