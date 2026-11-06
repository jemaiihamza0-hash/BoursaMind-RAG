"""Source-independent external event models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def normalize_datetime(value: datetime | str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class ExternalEventCreate(BaseModel):
    tenant_id: int
    source: str = Field(min_length=1, max_length=40)
    event_type: str = Field(min_length=1, max_length=80)
    title: str | None = Field(default=None, max_length=500)
    description: str | None = None
    location: str | None = Field(default=None, max_length=255)
    company: str | None = Field(default=None, max_length=255)
    sector: str | None = Field(default=None, max_length=120)
    indicator: str | None = Field(default=None, max_length=255)
    metric: str | None = Field(default=None, max_length=120)
    value: float | None = None
    unit: str | None = Field(default=None, max_length=40)
    period: str | None = Field(default=None, max_length=120)
    published_at: datetime | None = None
    observed_at: datetime | None = None
    source_url: str | None = Field(default=None, max_length=1000)
    external_id: str | None = Field(default=None, max_length=500)
    raw_data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("published_at", "observed_at", mode="before")
    @classmethod
    def _timestamps(cls, value: Any) -> datetime | None:
        return normalize_datetime(value)


class ExternalEventOut(ExternalEventCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    collected_at: datetime


class MetaPageOut(BaseModel):
    source: str = "meta"
    page_id: str
    name: str
    category: str | None = None
    connected: bool = True
    capabilities: dict[str, str] = Field(default_factory=dict)
    permissions: dict[str, str] = Field(default_factory=dict)


class MetaPostsOut(BaseModel):
    status: str
    provider: str = "meta"
    required_permission: str | None = None
    events: list[ExternalEventOut] = Field(default_factory=list)
