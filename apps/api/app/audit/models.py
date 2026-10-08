from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, UUIDPrimaryKeyMixin


class ActorType(StrEnum):
    USER = "USER"
    SYSTEM = "SYSTEM"
    INTEGRATION = "INTEGRATION"
    AI_AGENT = "AI_AGENT"
    GUEST = "GUEST"  # a guest acting through their portal link (own stay only)


class AuditLog(UUIDPrimaryKeyMixin, TenantMixin, Base):
    """Immutable record of *who* did *what* to *which* entity, and why it changed."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_organization_id_occurred_at", "organization_id", "occurred_at"),
        Index("ix_audit_logs_entity", "organization_id", "entity_type", "entity_id"),
        Index(
            "ix_audit_logs_org_entity_type_occurred_at",
            "organization_id",
            "entity_type",
            "occurred_at",
        ),
    )

    actor_type: Mapped[ActorType] = mapped_column(
        Enum(ActorType, native_enum=False, create_constraint=True, length=16, name="actor_type"),
        nullable=False,
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column()
    # Denormalised for display: survives profile changes and reads without a join.
    actor_label: Mapped[str | None] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Sanitised before insert (see audit.service.sanitize_metadata).
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )
    request_id: Mapped[str | None] = mapped_column(String(128))
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"))
