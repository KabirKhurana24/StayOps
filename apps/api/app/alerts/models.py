"""Operational alerts: something an operator must look at, raised by Autopilot.

Alerts are persisted facts (unlike Command Center signals, which are derived from
live state on every read). Open alerts appear among the Command Center signals and
in the notification bell; an operator acknowledges or resolves them. ``dedupe_key``
is unique per organization, so a retried workflow step raises an alert once.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class AlertSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


def _enum(cls: type[StrEnum], name: str) -> Enum:
    return Enum(cls, native_enum=False, create_constraint=True, length=16, name=name)


class OperationalAlert(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "operational_alerts"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "dedupe_key"),
        CheckConstraint("char_length(title) between 1 and 200", name="title_length"),
        Index("ix_operational_alerts_org_status", "organization_id", "status", "created_at"),
    )

    severity: Mapped[AlertSeverity] = mapped_column(
        _enum(AlertSeverity, "alert_severity"), nullable=False
    )
    status: Mapped[AlertStatus] = mapped_column(
        _enum(AlertStatus, "alert_status"), nullable=False, server_default=AlertStatus.OPEN.value
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(1000))
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    # What the alert is about (links in the UI) and what raised it.
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[uuid.UUID | None] = mapped_column()
    entity_label: Mapped[str | None] = mapped_column(String(200))
    property_id: Mapped[uuid.UUID | None] = mapped_column()
    workflow_id: Mapped[uuid.UUID | None] = mapped_column()
    execution_id: Mapped[uuid.UUID | None] = mapped_column()
    created_by_label: Mapped[str | None] = mapped_column(String(320))
    acknowledged_by_label: Mapped[str | None] = mapped_column(String(320))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
