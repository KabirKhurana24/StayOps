from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from app.auth.dependencies import CurrentUserDep, DbSession
from app.organizations.schemas import MembershipOut, OrganizationOut
from app.organizations.service import list_memberships

router = APIRouter(tags=["auth"])


class MeOut(BaseModel):
    id: uuid.UUID
    email: str | None
    full_name: str | None
    memberships: list[MembershipOut]


@router.get("/me", response_model=MeOut, summary="Current user and memberships")
def me(db: DbSession, user: CurrentUserDep) -> MeOut:
    return MeOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        memberships=[
            MembershipOut(
                organization=OrganizationOut.model_validate(org),
                role=member.role,
                joined_at=member.joined_at,
            )
            for org, member in list_memberships(db, user.id)
        ],
    )
