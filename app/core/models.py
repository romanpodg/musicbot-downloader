"""Provider-independent data transfer models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.core.enums import MusicProviderName, NativeCodec, NativeContainer


@dataclass(frozen=True, slots=True)
class NativeMediaInfo:
    """Native provider representation, separate from delivery quality profiles."""

    codec: NativeCodec | None = None
    container: NativeContainer | None = None
    bitrate_kbps: int | None = None


@dataclass(frozen=True, slots=True)
class NormalizedTrackMetadata:
    provider: MusicProviderName
    provider_track_id: str
    source_url: str
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    isrc: str | None = None
    duration_ms: int | None = None
    release_date: date | None = None
    explicit: bool | None = None
    native: NativeMediaInfo = field(default_factory=NativeMediaInfo)
    provider_metadata: dict[str, Any] = field(default_factory=dict)
