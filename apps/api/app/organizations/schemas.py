from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth.permissions import Permission
from app.organizations.models import Role


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    default_currency: str
    created_at: datetime


class MembershipOut(BaseModel):
    organization: OrganizationOut
    role: Role
    joined_at: datetime


class CurrentOrganizationOut(BaseModel):
    organization: OrganizationOut
    role: Role
    permissions: list[Permission]
    realtime_topic: str


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    default_currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value
