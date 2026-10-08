"""Guest portal access links.

Guests don't have StayOps accounts. An operator shares a per-stay portal link
containing a random, single-purpose token; the portal sends it as
``X-Guest-Token`` and the API resolves it to exactly one (organization, guest,
reservation). Only a SHA-256 hash is stored, so a database read can't be turned
into working links. Links expire after the stay and can be revoked; issuing a new
link revokes the previous one.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKeyConstraint, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, UUIDPrimaryKeyMixin


class GuestPortalLink(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "guest_portal_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "reservation_id"],
            ["reservations.organization_id", "reservations.id"],
            ondelete="CASCADE",
            name="fk_guest_portal_links_reservation_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "guest_id"],
            ["guests.organization_id", "guests.id"],
            ondelete="CASCADE",
            name="fk_guest_portal_links_guest_same_org",
        ),
        UniqueConstraint("token_hash"),
        Index(
            "ix_guest_portal_links_active",
            "organization_id",
            "reservation_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    reservation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    guest_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    created_by_label: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
