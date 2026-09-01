"""Canonical identity for one byte-affecting processed media artifact."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from app.core.download_preferences import EffectiveDownloadProfile

ARTIFACT_PROCESSING_VERSION = 2


@dataclass(frozen=True, slots=True)
class MediaArtifactSpec:
    """Immutable, deterministic description of requested output bytes."""

    effective_quality: str
    effective_format: str
    embed_metadata: bool
    embed_cover: bool
    metadata_identity: tuple[tuple[str, str], ...] = ()
    artwork_identity: str | None = None
    codec_parameters: tuple[tuple[str, str], ...] = ()
    processor_version: int = ARTIFACT_PROCESSING_VERSION

    @classmethod
    def from_profile(
        cls,
        profile: EffectiveDownloadProfile,
        *,
        metadata: dict[str, str] | None = None,
        artwork_identity: str | None = None,
        codec_parameters: dict[str, str] | None = None,
    ) -> MediaArtifactSpec:
        return cls(
            effective_quality=profile.effective_quality.value,
            effective_format=profile.effective_format.value,
            embed_metadata=profile.embed_metadata,
            embed_cover=profile.embed_cover,
            metadata_identity=tuple(sorted((metadata or {}).items()))
            if profile.embed_metadata
            else (),
            artwork_identity=artwork_identity if profile.embed_cover else None,
            codec_parameters=tuple(sorted((codec_parameters or {}).items())),
        )

    def canonical_json(self) -> str:
        return json.dumps(
            {
                "codec_parameters": dict(self.codec_parameters),
                "effective_format": self.effective_format,
                "effective_quality": self.effective_quality,
                "embed_cover": self.embed_cover,
                "embed_metadata": self.embed_metadata,
                "metadata_identity": dict(self.metadata_identity),
                "artwork_identity": self.artwork_identity,
                "processor_version": self.processor_version,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @property
    def fingerprint(self) -> str:
        return sha256(self.canonical_json().encode("utf-8")).hexdigest()


def legacy_quality_fingerprint(quality: Any) -> str:
    """Stable identity for pre-Stage-26 queue callers."""

    value = getattr(quality, "value", quality)
    payload = json.dumps(
        {"effective_quality": str(value), "processor_version": 1},
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()
