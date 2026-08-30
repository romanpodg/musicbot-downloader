"""Stable Stage 24 cache identity for one produced Telegram artifact."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

ARTIFACT_PROCESSING_VERSION = 1


@dataclass(frozen=True, slots=True)
class TelegramCacheKey:
    provider: str
    provider_media_id: str
    effective_quality: str
    effective_format: str
    delivery_mode: str
    embed_metadata: bool
    embed_cover: bool
    artifact_processing_version: int = ARTIFACT_PROCESSING_VERSION

    @classmethod
    def from_request(cls, request: Any) -> TelegramCacheKey | None:
        values = (
            request.provider,
            request.provider_media_id,
            request.effective_quality,
            request.effective_format,
            request.delivery_mode,
            request.embed_metadata,
            request.embed_cover,
        )
        if any(value is None for value in values):
            return None
        return cls(
            provider=str(
                request.provider.value if hasattr(request.provider, "value") else request.provider
            ),
            provider_media_id=str(request.provider_media_id),
            effective_quality=str(
                request.effective_quality.value
                if hasattr(request.effective_quality, "value")
                else request.effective_quality
            ),
            effective_format=str(
                request.effective_format.value
                if hasattr(request.effective_format, "value")
                else request.effective_format
            ),
            delivery_mode=str(
                request.delivery_mode.value
                if hasattr(request.delivery_mode, "value")
                else request.delivery_mode
            ),
            embed_metadata=bool(request.embed_metadata),
            embed_cover=bool(request.embed_cover),
        )

    def canonical_json(self) -> str:
        return json.dumps(
            {
                "artifact_processing_version": self.artifact_processing_version,
                "delivery_mode": self.delivery_mode,
                "effective_format": self.effective_format,
                "effective_quality": self.effective_quality,
                "embed_cover": self.embed_cover,
                "embed_metadata": self.embed_metadata,
                "provider": self.provider,
                "provider_media_id": self.provider_media_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def fingerprint(self) -> str:
        return sha256(self.canonical_json().encode("utf-8")).hexdigest()
