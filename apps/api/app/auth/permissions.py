"""Role → permission mapping.

Endpoints depend on *permissions*, not roles, so new roles (or per-member
overrides) can be introduced without touching routers.

Operations (Phase 3) split work permissions in two:

* ``*:manage`` — create, edit, assign, cancel, inspect (OWNER/ADMIN/OPERATOR).
* ``*:work``   — act on work *assigned to you*: start, complete, update the
  checklist, report an issue (STAFF). The "assigned to you" part is enforced in
  the services, because it depends on the row, not just the role.
"""

from __future__ import annotations

from enum import StrEnum

from app.organizations.models import Role


class Permission(StrEnum):
    ORG_READ = "org:read"
    ORG_MANAGE = "org:manage"
    PROPERTIES_READ = "properties:read"
    PROPERTIES_WRITE = "properties:write"
    UNITS_WRITE = "units:write"
    GUESTS_READ = "guests:read"
    GUESTS_WRITE = "guests:write"
    RESERVATIONS_READ = "reservations:read"
    RESERVATIONS_WRITE = "reservations:write"
    ACTIVITY_READ = "activity:read"
    TASKS_READ = "tasks:read"
    TASKS_MANAGE = "tasks:manage"
    TASKS_WORK = "tasks:work"
    MAINTENANCE_READ = "maintenance:read"
    MAINTENANCE_MANAGE = "maintenance:manage"
    MAINTENANCE_WORK = "maintenance:work"
    INBOX_READ = "inbox:read"
    INBOX_WRITE = "inbox:write"
    GUEST_REQUESTS_MANAGE = "guest_requests:manage"
    KNOWLEDGE_WRITE = "knowledge:write"
    COPILOT_USE = "copilot:use"
    # Autopilot (Phase 6): read workflows/executions; author, publish, pause, retry;
    # decide actions a workflow paused for.
    AUTOMATIONS_READ = "automations:read"
    AUTOMATIONS_MANAGE = "automations:manage"
    AUTOMATIONS_APPROVE = "automations:approve"


_READ = {
    Permission.ORG_READ,
    Permission.PROPERTIES_READ,
    Permission.GUESTS_READ,
    Permission.RESERVATIONS_READ,
    Permission.ACTIVITY_READ,
    Permission.TASKS_READ,
    Permission.MAINTENANCE_READ,
}
# Guest communications are front-of-house: viewers may read the inbox, staff (who
# work assigned tasks) don't see guest conversations at all.
_VIEW = _READ | {Permission.INBOX_READ, Permission.COPILOT_USE, Permission.AUTOMATIONS_READ}
# Staff see the operation (properties, calendar) but only act on their own work;
# task/work-order visibility is narrowed to "mine" in the services.
_STAFF = _READ | {Permission.TASKS_WORK, Permission.MAINTENANCE_WORK}
_OPERATE = _STAFF | {
    Permission.GUESTS_WRITE,
    Permission.RESERVATIONS_WRITE,
    Permission.UNITS_WRITE,
    Permission.TASKS_MANAGE,
    Permission.MAINTENANCE_MANAGE,
    Permission.INBOX_READ,
    Permission.INBOX_WRITE,
    Permission.GUEST_REQUESTS_MANAGE,
    Permission.KNOWLEDGE_WRITE,
    Permission.COPILOT_USE,
    Permission.AUTOMATIONS_READ,
    Permission.AUTOMATIONS_MANAGE,
    Permission.AUTOMATIONS_APPROVE,
}
_ADMIN = _OPERATE | {Permission.PROPERTIES_WRITE, Permission.ORG_MANAGE}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OWNER: frozenset(_ADMIN),
    Role.ADMIN: frozenset(_ADMIN),
    Role.OPERATOR: frozenset(_OPERATE),
    Role.STAFF: frozenset(_STAFF),
    Role.VIEWER: frozenset(_VIEW),
}

# Members who can be given operational work (tasks, work orders).
ASSIGNABLE_ROLES = frozenset({Role.OWNER, Role.ADMIN, Role.OPERATOR, Role.STAFF})


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]
