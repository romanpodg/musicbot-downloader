"""HTTP-only request and response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)


class HealthResponse(BaseModel):
    status: str


class DeepLinkResponse(BaseModel):
    target_type: str
    status: str
    start_parameter: str
    deep_link_url: str
    created: bool
    created_at: datetime
    revoked_at: datetime | None


class DeepLinkTargetResponse(BaseModel):
    target_type: str
    status: str
    start_parameter: str
    track_id: int | None = None
    album_provider: str | None = None
    album_provider_id: str | None = None
    created_at: datetime
    revoked_at: datetime | None
