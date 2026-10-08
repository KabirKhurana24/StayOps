"""Import every model so `Base.metadata` is complete (Alembic, tests)."""

from app.audit.models import AuditLog
from app.cleaning.models import Cleaning
from app.db.base import Base
from app.events.models import Event
from app.guest_portal.models import GuestPortalLink
from app.guest_requests.models import GuestRequest
from app.guests.models import Guest
from app.inbox.models import Conversation, Message
from app.knowledge.models import PropertyKnowledge
from app.maintenance.models import WorkOrder
from app.organizations.models import Organization, OrganizationMember, Profile
from app.properties.models import Property, Unit
from app.reservations.models import Reservation
from app.tasks.models import Task

__all__ = [
    "AuditLog",
    "Base",
    "Cleaning",
    "Conversation",
    "Event",
    "Guest",
    "GuestPortalLink",
    "GuestRequest",
    "Message",
    "Organization",
    "OrganizationMember",
    "Profile",
    "Property",
    "PropertyKnowledge",
    "Reservation",
    "Task",
    "Unit",
    "WorkOrder",
]
