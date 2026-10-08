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

    @classmethod
    def guest(cls, guest_id: uuid.UUID, label: str) -> Actor:
        return cls(type=ActorType.GUEST, id=guest_id, label=label)


@dataclass(frozen=True, slots=True)
class GuestScope:
    """The one stay a guest-portal request may touch (resolved from the link token)."""

    guest_id: uuid.UUID
    reservation_id: uuid.UUID
    link_id: uuid.UUID | None = None  # None for seeded/imported guest activity


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
    # Set only for guest-portal requests. Such contexts carry the VIEWER role (no
    # write permission anywhere) and guest-facing services scope every query to
    # this stay; staff routes never accept them (they require a member JWT).
    guest_scope: GuestScope | None = None

    @classmethod
    def system(cls, organization_id: uuid.UUID, label: str = "Automation") -> TenantContext:
        """Context for automation (event handlers): acts with operator authority."""
        return cls(
            organization_id=organization_id,
            role=Role.ADMIN,
            actor=Actor.system(label),
            source="worker",
        )

    @classmethod
    def for_guest(cls, organization_id: uuid.UUID, scope: GuestScope, label: str) -> TenantContext:
        return cls(
            organization_id=organization_id,
            role=Role.VIEWER,
            actor=Actor.guest(scope.guest_id, label),
            source="guest_portal",
            guest_scope=scope,
        )

    @property
    def is_guest(self) -> bool:
        return self.guest_scope is not None

    @property
    def is_staff(self) -> bool:
        """Staff only see and act on work assigned to them."""
        return self.role == Role.STAFF

    def can(self, permission: Permission) -> bool:
        return has_permission(self.role, permission)

    def require(self, permission: Permission) -> None:
        if not self.can(permission):
            raise PermissionDeniedError()
