from __future__ import annotations

from typing import Any

from sqlalchemy import CheckConstraint, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Guest(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "guests"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        # One guest record per email per organization (case-insensitive).
        Index(
            "uq_guests_organization_id_email_ci",
            "organization_id",
            func.lower(text("email")),
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
        Index("ix_guests_organization_id_last_name", "organization_id", "last_name"),
        CheckConstraint("char_length(first_name) between 1 and 100", name="first_name_length"),
        CheckConstraint("char_length(last_name) between 1 and 100", name="last_name_length"),
    )

    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(32))
    # Structured stay preferences (see guests.schemas.GuestPreferences). Shared with
    # the team; never shown to other guests.
    preferences: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Private team notes. Internal only: never exposed through the guest portal or
    # to AI guest agents.
    notes: Mapped[str | None] = mapped_column(String(4000))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
