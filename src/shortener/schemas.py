"""Request and response models for the shortener API."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class CreateLinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)
    alias: str | None = None
    ttl_seconds: int | None = Field(default=None, ge=1, le=31_536_000)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _aware(value)

    @model_validator(mode="after")
    def _normalize(self):
        if self.alias is not None:
            self.alias = self.alias.strip() or None
        if self.ttl_seconds is not None and self.expires_at is not None:
            raise ValueError("Provide only one of ttl_seconds or expires_at")
        return self


class LinkOut(BaseModel):
    code: str
    target_url: str
    short_url: str
    created_at: datetime
    expires_at: datetime | None
    disabled: bool
    status: str
    custom_alias: bool


class CreateLinkOut(LinkOut):
    replayed: bool = False


class ReferrerCount(BaseModel):
    referrer: str
    clicks: int


class DayCount(BaseModel):
    date: str
    clicks: int


class StatsOut(BaseModel):
    code: str
    total_clicks: int
    last_access: datetime | None
    top_referrers: list[ReferrerCount]
    clicks_per_day: list[DayCount]
