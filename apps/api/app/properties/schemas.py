from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.time import is_valid_timezone
from app.properties.models import PropertyStatus, UnitStatus, UnitType


def _validate_tz(value: str) -> str:
    if not is_valid_timezone(value):
        raise ValueError("must be a valid IANA timezone, e.g. 'Europe/Amsterdam'")
    return value


class _NoExplicitNulls(BaseModel):
    """PATCH bodies: omitted fields are untouched; explicit null is rejected."""

    @model_validator(mode="before")
    @classmethod
    def _reject_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            nulls = [k for k, v in data.items() if v is None]
            if nulls:
                raise ValueError(f"fields cannot be null: {', '.join(sorted(nulls))}")
        return data


class PropertyCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    address: str = Field(min_length=1, max_length=500)
    timezone: str = Field(max_length=64)
    status: PropertyStatus = PropertyStatus.ACTIVE
    default_check_in_time: time = time(15, 0)
    default_check_out_time: time = time(11, 0)

    _tz = field_validator("timezone")(_validate_tz)


class PropertyUpdate(_NoExplicitNulls):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    address: str | None = Field(default=None, min_length=1, max_length=500)
    timezone: str | None = Field(default=None, max_length=64)
    status: PropertyStatus | None = None
    default_check_in_time: time | None = None
    default_check_out_time: time | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str | None) -> str | None:
        return _validate_tz(value) if value is not None else value


class NextArrival(BaseModel):
    reservation_id: uuid.UUID
    check_in: datetime
    guest_name: str
    unit_name: str


class PropertyStats(BaseModel):
    """Live operational state of a property (see properties.stats)."""

    active_units: int = 0
    maintenance_units: int = 0
    occupied_units: int = 0
    arrivals_today: int = 0
    departures_today: int = 0
    next_arrival: NextArrival | None = None


class PropertyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    address: str
    timezone: str
    status: PropertyStatus
    default_check_in_time: time
    default_check_out_time: time
    unit_count: int = 0
    stats: PropertyStats | None = None
    created_at: datetime
    updated_at: datetime


class UnitCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    property_id: uuid.UUID
    name: str = Field(min_length=1, max_length=100)
    type: UnitType = UnitType.APARTMENT
    status: UnitStatus = UnitStatus.ACTIVE


class UnitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    property_id: uuid.UUID
    name: str
    type: UnitType
    status: UnitStatus
    # Derived, see app.operations.unit_state:
    # READY | OCCUPIED | CLEANING | MAINTENANCE | OUT_OF_SERVICE
    operational_status: str = "READY"
    maintenance_blocked: bool = False
    created_at: datetime
    updated_at: datetime
