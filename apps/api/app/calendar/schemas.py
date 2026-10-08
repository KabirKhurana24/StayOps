from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel

from app.properties.models import PropertyStatus, UnitStatus, UnitType
from app.reservations.schemas import ReservationOut


class CalendarUnit(BaseModel):
    id: uuid.UUID
    name: str
    type: UnitType
    status: UnitStatus


class CalendarProperty(BaseModel):
    id: uuid.UUID
    name: str
    timezone: str
    status: PropertyStatus
    units: list[CalendarUnit]


class CalendarOut(BaseModel):
    """Everything one calendar screen needs, in a single request."""

    start: date
    end: date  # exclusive
    properties: list[CalendarProperty]
    reservations: list[ReservationOut]
