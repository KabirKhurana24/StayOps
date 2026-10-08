from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


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


class GuestPreferences(BaseModel):
    """Structured stay preferences, shared with the team (never with other guests)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    preferred_language: str | None = Field(default=None, max_length=35)
    contact_channel: Literal["IN_APP", "EMAIL", "SMS", "WHATSAPP", "PHONE"] | None = None
    usually_requests_early_check_in: bool = False
    usually_requests_late_checkout: bool = False
    bedding: str | None = Field(default=None, max_length=200)
    accessibility: str | None = Field(default=None, max_length=500)
    dietary: str | None = Field(default=None, max_length=200)
    occasions: str | None = Field(default=None, max_length=200)
    other: str | None = Field(default=None, max_length=1000)


class GuestUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32, pattern=r"^\+?[0-9 ()./-]{5,32}$")
    preferences: GuestPreferences | None = None
    notes: str | None = Field(default=None, max_length=4000)
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _reject_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            nullable = ("email", "phone", "notes")
            nulls = [k for k, v in data.items() if v is None and k not in nullable]
            if nulls:
                raise ValueError(f"fields cannot be null: {', '.join(sorted(nulls))}")
        return data

    @field_validator("email")
    @classmethod
    def _lower(cls, value: str | None) -> str | None:
        return value.lower() if value else None

    @field_validator("phone", "notes", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value


class GuestPropertyStays(BaseModel):
    property_id: uuid.UUID
    property_name: str
    stays: int
    last_check_out: datetime | None


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
    properties: list[GuestPropertyStays] = Field(default_factory=list)


class GuestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    first_name: str
    last_name: str
    email: str | None
    phone: str | None
    preferences: dict[str, Any] = Field(default_factory=dict)
    # Internal team notes: staff API only (the guest portal has its own schemas).
    notes: str | None = None
    version: int = 1
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
