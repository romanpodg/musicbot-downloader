"""Provider-independent data transfer models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.core.enums import (
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    ProviderDiscoveryStatus,
    ProviderResolutionStatus,
    ProviderRuntimeStatus,
    TrackEvidenceCode,
    TrackMatchDecision,
)


@dataclass(frozen=True, slots=True)
class NativeMediaInfo:
    """Native provider representation, separate from delivery quality profiles."""

    codec: NativeCodec | None = None
    container: NativeContainer | None = None
    bitrate_kbps: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderMediaCapabilities:
    """Provider implementation facts; unknown and unsupported stay distinct."""

    known: bool
    supports_lossy: bool | None = None
    supports_lossless: bool | None = None
    native_codecs: frozenset[NativeCodec] = field(default_factory=frozenset)
    native_containers: frozenset[NativeContainer] = field(default_factory=frozenset)
    bitrate_options_kbps: frozenset[int] = field(default_factory=frozenset)
    max_sample_rate_hz: int | None = None
    max_bit_depth: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    metadata_supported: bool
    search_supported: bool
    download_supported: bool
    requires_auth: bool | None
    media: ProviderMediaCapabilities


@dataclass(frozen=True, slots=True)
class ProviderSourceCheck:
    status: ProviderRuntimeStatus
    native_media_info: NativeMediaInfo | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class DownloadProviderCandidate:
    track_id: int
    track_source_id: int
    provider: MusicProviderName
    provider_track_id: str
    runtime_status: ProviderRuntimeStatus
    capabilities: ProviderCapabilities
    native_media_info: NativeMediaInfo | None = None


@dataclass(frozen=True, slots=True)
class ProviderCandidateFailure:
    track_id: int
    track_source_id: int
    provider: MusicProviderName
    provider_track_id: str
    runtime_status: ProviderRuntimeStatus
    capabilities: ProviderCapabilities
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderResolutionResult:
    track_id: int
    status: ProviderResolutionStatus
    candidates: tuple[DownloadProviderCandidate, ...]
    failures: tuple[ProviderCandidateFailure, ...]


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


@dataclass(frozen=True, slots=True)
class TrackIdentity:
    """Identity of one specific recording/version, independent of provider."""

    provider: MusicProviderName | None
    provider_track_id: str | None
    title: str | None
    artist: str | None
    album: str | None
    isrc: str | None
    duration_ms: int | None
    explicit: bool | None
    normalized_title: str | None
    normalized_artist: str | None
    version_markers: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class TrackMatchEvidence:
    code: TrackEvidenceCode
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class TrackMatchCandidate:
    track_id: int
    identity: TrackIdentity
    evidence: tuple[TrackMatchEvidence, ...]
    compatible: bool
    incomplete: bool


@dataclass(frozen=True, slots=True)
class TrackMatchResult:
    decision: TrackMatchDecision
    matched_track_id: int | None
    candidates: tuple[TrackMatchCandidate, ...]


@dataclass(frozen=True, slots=True)
class TrackSearchRequest:
    target_provider: MusicProviderName
    query: str
    limit: int


@dataclass(frozen=True, slots=True)
class TrackSearchCandidate:
    provider: MusicProviderName
    provider_track_id: str
    url: str
    title: str | None = None
    artist: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderDiscoveryResult:
    provider: MusicProviderName
    status: ProviderDiscoveryStatus
    provider_track_id: str | None = None
    evidence: tuple[TrackMatchEvidence, ...] = ()
    error_code: str | None = None
