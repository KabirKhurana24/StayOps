"""FastAPI dependencies: authentication → membership → permission."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.auth.context import Actor, CurrentUser, TenantContext
from app.auth.permissions import Permission
from app.auth.tokens import verify_access_token
from app.config.settings import Settings, get_settings
from app.core.errors import AuthenticationError, NoOrganizationError, PermissionDeniedError
from app.db.session import get_db
from app.organizations.models import OrganizationMember, Profile

_bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError()
    token = verify_access_token(credentials.credentials, settings)

    # Provision/refresh the profile mirror from verified claims (no-op if unchanged).
    stmt = insert(Profile).values(id=token.user_id, email=token.email, full_name=token.full_name)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Profile.id],
        set_={"email": stmt.excluded.email, "full_name": stmt.excluded.full_name},
        where=(
            Profile.email.is_distinct_from(stmt.excluded.email)
            | Profile.full_name.is_distinct_from(stmt.excluded.full_name)
        ),
    )
    db.execute(stmt)
    db.commit()
    return CurrentUser(id=token.user_id, email=token.email, full_name=token.full_name)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def get_tenant_context(
    db: DbSession,
    user: CurrentUserDep,
    x_organization_id: Annotated[uuid.UUID | None, Header()] = None,
) -> TenantContext:
    """Resolve the active organization from the membership table.

    The client may choose an organization with ``X-Organization-Id``; otherwise the
    user's earliest membership is used. Either way the membership is verified
    server-side — a header naming a foreign organization is rejected.
    """
    query = select(OrganizationMember).where(OrganizationMember.user_id == user.id)
    if x_organization_id is not None:
        member = db.scalars(
            query.where(OrganizationMember.organization_id == x_organization_id)
        ).first()
        if member is None:
            raise PermissionDeniedError("You are not a member of this organization.")
    else:
        member = db.scalars(
            query.order_by(OrganizationMember.joined_at, OrganizationMember.id)
        ).first()
        if member is None:
            raise NoOrganizationError()

    return TenantContext(
        organization_id=member.organization_id, role=member.role, actor=Actor.user(user)
    )


def require(permission: Permission) -> Callable[..., TenantContext]:
    def dependency(
        ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    ) -> TenantContext:
        ctx.require(permission)
        return ctx

    dependency.__name__ = f"require_{permission.value.replace(':', '_')}"
    return dependency
