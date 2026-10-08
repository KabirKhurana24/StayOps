from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.knowledge.models import KnowledgeCategory, KnowledgeVisibility


class KnowledgeCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    category: KnowledgeCategory
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=4000)
    visibility: KnowledgeVisibility = KnowledgeVisibility.PUBLIC
    unit_id: uuid.UUID | None = Field(default=None, description="Only for stays in this unit.")
    sort_order: int = Field(default=0, ge=0, le=1000)


class KnowledgeUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    category: KnowledgeCategory | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    visibility: KnowledgeVisibility | None = None
    sort_order: int | None = Field(default=None, ge=0, le=1000)
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _reject_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            nulls = [k for k, v in data.items() if v is None]
            if nulls:
                raise ValueError(f"fields cannot be null: {', '.join(sorted(nulls))}")
        return data


class KnowledgeOut(BaseModel):
    id: uuid.UUID
    property_id: uuid.UUID
    unit_id: uuid.UUID | None
    unit_name: str | None
    category: KnowledgeCategory
    title: str
    content: str
    visibility: KnowledgeVisibility
    sort_order: int
    updated_by_label: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class GuestKnowledgeEntry(BaseModel):
    """What a guest sees: no visibility flags, authors or internal ids beyond the entry."""

    id: uuid.UUID
    category: KnowledgeCategory
    title: str
    content: str
    updated_at: datetime
