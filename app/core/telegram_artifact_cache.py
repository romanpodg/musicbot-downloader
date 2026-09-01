"""Stable Stage 24 cache identity for one produced Telegram artifact."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from app.core.media_artifact import MediaArtifactSpec

ARTIFACT_PROCESSING_VERSION = 2


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
    metadata_identity: tuple[tuple[str, str], ...] = ()
    artwork_identity: str | None = None

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
        metadata: dict[str, str] = {}
        for key in ("media_title", "media_artist", "media_album"):
            value = getattr(request, key, None)
            if value:
                metadata[key] = str(value)
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
            metadata_identity=tuple(sorted(metadata.items())) if request.embed_metadata else (),
        )

    @classmethod
    def from_artifact_spec(
        cls, *, provider: str, provider_media_id: str, spec: MediaArtifactSpec
    ) -> TelegramCacheKey:
        return cls(
            provider=provider,
            provider_media_id=provider_media_id,
            effective_quality=spec.effective_quality,
            effective_format=spec.effective_format,
            delivery_mode="",
            embed_metadata=spec.embed_metadata,
            embed_cover=spec.embed_cover,
            artifact_processing_version=spec.processor_version,
            metadata_identity=spec.metadata_identity,
            artwork_identity=spec.artwork_identity,
        )

    def canonical_json(self) -> str:
        return json.dumps(
            {
                "artifact_processing_version": self.artifact_processing_version,
                "effective_format": self.effective_format,
                "effective_quality": self.effective_quality,
                "embed_cover": self.embed_cover,
                "embed_metadata": self.embed_metadata,
                "metadata_identity": dict(self.metadata_identity),
                "artwork_identity": self.artwork_identity,
                "provider": self.provider,
                "provider_media_id": self.provider_media_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def fingerprint(self) -> str:
        return sha256(self.canonical_json().encode("utf-8")).hexdigest()
