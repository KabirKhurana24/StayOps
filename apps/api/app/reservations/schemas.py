from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.guests.schemas import GuestCreate, GuestSummary
from app.reservations.models import ReservationSource, ReservationStatus


def _to_utc(value: datetime | None) -> datetime | None:
    return value.astimezone(UTC) if value is not None else None


def _quantize(value: Decimal | None) -> Decimal | None:
    return value.quantize(Decimal("0.01")) if value is not None else None


# Normalised on the way in so diffs compare instants/amounts, not representations.
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]
Money = Annotated[
    Decimal,
    Field(ge=0, le=Decimal("9999999999.99"), decimal_places=2),
    AfterValidator(_quantize),
]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
ConfirmationCode = Annotated[str, Field(pattern=r"^[A-Z0-9-]{4,32}$")]


class ReservationCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    unit_id: uuid.UUID
    guest_id: uuid.UUID | None = None
    guest: GuestCreate | None = Field(
        default=None, description="Create the guest inline instead of passing guest_id."
    )
    # Timezone-aware instants only; naive datetimes are rejected with 422.
    check_in: UtcDatetime
    check_out: UtcDatetime
    status: ReservationStatus = ReservationStatus.CONFIRMED
    adults: int = Field(default=1, ge=1, le=50)
    children: int = Field(default=0, ge=0, le=50)
    source: ReservationSource = ReservationSource.DIRECT
    total_amount: Money = Decimal("0.00")
    currency: Currency | None = None  # defaults to the organization currency
    confirmation_code: ConfirmationCode | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _validate(self) -> ReservationCreate:
        if (self.guest_id is None) == (self.guest is None):
            raise ValueError("provide exactly one of guest_id or guest")
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        if self.status not in (ReservationStatus.PENDING, ReservationStatus.CONFIRMED):
            raise ValueError("new reservations must be PENDING or CONFIRMED")
        return self

    @field_validator("confirmation_code", mode="before")
    @classmethod
    def _upper(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class ReservationUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    unit_id: uuid.UUID | None = None
    guest_id: uuid.UUID | None = None
    check_in: UtcDatetime | None = None
    check_out: UtcDatetime | None = None
    status: ReservationStatus | None = None
    adults: int | None = Field(default=None, ge=1, le=50)
    children: int | None = Field(default=None, ge=0, le=50)
    source: ReservationSource | None = None
    total_amount: Money | None = None
    currency: Currency | None = None
    notes: str | None = Field(default=None, max_length=2000)
    expected_version: int | None = Field(
        default=None, ge=1, description="Optimistic concurrency: reject if the record moved on."
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            nulls = [k for k, v in data.items() if v is None and k != "notes"]
            if nulls:
                raise ValueError(f"fields cannot be null: {', '.join(sorted(nulls))}")
        return data


class PropertyRef(BaseModel):
    id: uuid.UUID
    name: str
    timezone: str


class UnitRef(BaseModel):
    id: uuid.UUID
    name: str


class ReservationOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    confirmation_code: str
    status: ReservationStatus
    check_in: datetime
    check_out: datetime
    nights: int
    adults: int
    children: int
    source: ReservationSource
    total_amount: Decimal
    currency: str
    notes: str | None
    version: int
    property: PropertyRef
    unit: UnitRef
    guest: GuestSummary
    created_at: datetime
    updated_at: datetime


class ReservationAction(BaseModel):
    """Body for lifecycle actions (confirm / check-in / check-out / cancel)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    expected_version: int | None = Field(
        default=None, ge=1, description="Optimistic concurrency: reject if the record moved on."
    )
    reason: str | None = Field(
        default=None, max_length=500, description="Recorded in the audit log."
    )


class ConflictOut(BaseModel):
    reservation_id: uuid.UUID
    confirmation_code: str
    guest_name: str
    status: ReservationStatus
    check_in: datetime
    check_out: datetime


class UnitAvailabilityOut(BaseModel):
    unit_id: uuid.UUID
    name: str
    status: str
    available: bool
    bookable: bool
    reason: str | None
    conflicts: list[ConflictOut]


class AvailabilityOut(BaseModel):
    property: PropertyRef
    check_in: datetime
    check_out: datetime
    units: list[UnitAvailabilityOut]
