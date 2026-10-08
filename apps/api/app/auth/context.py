from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.audit.models import ActorType
from app.auth.permissions import Permission, has_permission
from app.core.errors import PermissionDeniedError
from app.organizations.models import Role


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: uuid.UUID
    email: str | None
    full_name: str | None

    @property
    def label(self) -> str:
        return self.full_name or self.email or str(self.id)


@dataclass(frozen=True, slots=True)
class Actor:
    """Who is performing a domain action. Users today; AI agents/integrations later."""

    type: ActorType
    id: uuid.UUID | None
    label: str | None

    @classmethod
    def user(cls, user: CurrentUser) -> Actor:
        return cls(type=ActorType.USER, id=user.id, label=user.label)

    @classmethod
    def system(cls, label: str = "system") -> Actor:
        return cls(type=ActorType.SYSTEM, id=None, label=label)


@dataclass(frozen=True, slots=True)
class TenantContext:
    """The resolved, authorized scope of a request.

    Services receive this object and MUST scope every query by
    ``organization_id``. It is only constructed after membership is verified
    against the database.
    """

    organization_id: uuid.UUID
    role: Role
    actor: Actor
    source: str = "api"

    @classmethod
    def system(cls, organization_id: uuid.UUID, label: str = "Automation") -> TenantContext:
        """Context for automation (event handlers): acts with operator authority."""
        return cls(
            organization_id=organization_id,
            role=Role.ADMIN,
            actor=Actor.system(label),
            source="worker",
        )

    @property
    def is_staff(self) -> bool:
        """Staff only see and act on work assigned to them."""
        return self.role == Role.STAFF

    def can(self, permission: Permission) -> bool:
        return has_permission(self.role, permission)

    def require(self, permission: Permission) -> None:
        if not self.can(permission):
            raise PermissionDeniedError()
