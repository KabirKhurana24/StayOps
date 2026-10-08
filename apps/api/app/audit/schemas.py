from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.audit.models import ActorType


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    actor_type: ActorType
    actor_id: uuid.UUID | None
    actor_label: str | None
    action: str
    entity_type: str
    entity_id: uuid.UUID
    occurred_at: datetime
    metadata: dict[str, Any] = Field(validation_alias="metadata_")
    request_id: str | None
    event_id: uuid.UUID | None
