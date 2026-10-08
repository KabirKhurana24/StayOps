from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, String, UniqueConstraint, func, text
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
