from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.auth.context import TenantContext
from app.auth.dependencies import CurrentUserDep, DbSession, require
from app.auth.permissions import ROLE_PERMISSIONS, Permission
from app.events.dispatcher import commit_and_dispatch
from app.organizations import service
from app.organizations.schemas import (
    CurrentOrganizationOut,
    MembershipOut,
    OrganizationCreate,
    OrganizationOut,
)

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.get("", response_model=list[MembershipOut], summary="Organizations I belong to")
def list_my_organizations(db: DbSession, user: CurrentUserDep) -> list[MembershipOut]:
    return [
        MembershipOut(
            organization=OrganizationOut.model_validate(org),
            role=member.role,
            joined_at=member.joined_at,
        )
        for org, member in service.list_memberships(db, user.id)
    ]


@router.get("/me", response_model=CurrentOrganizationOut, summary="Active organization")
def current_organization(
    db: DbSession,
    ctx: Annotated[TenantContext, Depends(require(Permission.ORG_READ))],
) -> CurrentOrganizationOut:
    org = service.get_organization(db, ctx)
    return CurrentOrganizationOut(
        organization=OrganizationOut.model_validate(org),
        role=ctx.role,
        permissions=sorted(ROLE_PERMISSIONS[ctx.role]),
        realtime_topic=service.realtime_topic(org.id),
    )


@router.post(
    "",
    response_model=OrganizationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an organization (caller becomes OWNER)",
)
def create_organization(
    db: DbSession, user: CurrentUserDep, body: OrganizationCreate
) -> OrganizationOut:
    org = service.create_organization(db, user, body)
    commit_and_dispatch(db)
    return OrganizationOut.model_validate(org)
