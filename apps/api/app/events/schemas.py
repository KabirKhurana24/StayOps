from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.events.models import EventStatus


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    event_type: str
    aggregate_type: str
    aggregate_id: uuid.UUID
    occurred_at: datetime
    created_at: datetime
    payload: dict[str, Any]
    source: str
    actor_id: uuid.UUID | None
    status: EventStatus
    attempts: int
    processed_at: datetime | None
