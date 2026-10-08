from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class GuestCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32, pattern=r"^\+?[0-9 ()./-]{5,32}$")

    @field_validator("email")
    @classmethod
    def _lower(cls, value: str | None) -> str | None:
        return value.lower() if value else None

    @field_validator("phone", mode="before")
    @classmethod
    def _blank_phone(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value


class GuestStayRef(BaseModel):
    reservation_id: uuid.UUID
    confirmation_code: str
    status: str
    check_in: datetime
    check_out: datetime
    property_id: uuid.UUID
    property_name: str
    property_timezone: str
    unit_name: str


class GuestStats(BaseModel):
    total_stays: int = 0  # non-cancelled reservations
    completed_stays: int = 0
    last_stay_at: datetime | None = None
    next_stay: GuestStayRef | None = None  # current or upcoming


class GuestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    first_name: str
    last_name: str
    email: str | None
    phone: str | None
    stats: GuestStats | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class GuestSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    email: str | None
    phone: str | None
