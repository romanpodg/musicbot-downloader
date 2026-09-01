"""Provider-independent data transfer models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.core.enums import (
    BatchSourceType,
    DeliveryPreparationStatus,
    DownloadAttemptStatus,
    DownloadFailureCode,
    DownloadPlanOperation,
    DownloadPlanReadiness,
    DownloadPlanReason,
    FormatPreference,
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    ProviderDiscoveryStatus,
    ProviderHealthErrorCode,
    ProviderHealthStatus,
    ProviderResolutionStatus,
    ProviderRuntimeStatus,
    QualityCandidateRejectionReason,
    QualityPreference,
    QualityProfile,
    QualityResolutionStatus,
    QueueJobStatus,
    SourceValidationConfidence,
    SubscriberStatus,
    TelegramCacheStatus,
    TelegramMediaKind,
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

    known: bool = True
    supports_lossy: bool | None = None
    supports_lossless: bool | None = None
    native_codecs: frozenset[NativeCodec] = field(default_factory=frozenset)
    native_containers: frozenset[NativeContainer] = field(default_factory=frozenset)
    bitrate_options_kbps: frozenset[int] = field(default_factory=frozenset)
    potential_media: tuple[NativeMediaInfo, ...] = ()
    max_sample_rate_hz: int | None = None
    max_bit_depth: int | None = None
    # Optional provider-neutral translation for item-specific capabilities.
    qualities: frozenset[QualityPreference] = field(default_factory=frozenset)
    formats: frozenset[FormatPreference] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    metadata_supported: bool = True
    search_supported: bool = True
    download_supported: bool = True
    requires_auth: bool | None = None
    media: ProviderMediaCapabilities = field(default_factory=ProviderMediaCapabilities)
    # Stage 22 provider-neutral capability translation.  Empty values retain
    # compatibility with older adapters; the resolver derives them from media.
    qualities: frozenset[QualityPreference] = field(default_factory=frozenset)
    formats: frozenset[FormatPreference] = field(default_factory=frozenset)


# Narrow alias used by Stage 22 callers without introducing a second media model.
MediaCapabilities = ProviderMediaCapabilities


@dataclass(frozen=True, slots=True)
class ProviderSourceCheck:
    status: ProviderRuntimeStatus
    native_media_info: NativeMediaInfo | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderHealthEntry:
    """Sanitized provider-level readiness; never contains account identity or secrets."""

    provider: MusicProviderName
    status: ProviderHealthStatus
    requires_authentication: bool
    download_supported: bool
    error_code: ProviderHealthErrorCode | None = None


@dataclass(frozen=True, slots=True)
class ProviderHealthSnapshot:
    checked_at: datetime
    entries: tuple[ProviderHealthEntry, ...]
    duration_ms: int


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
class SourceMediaRequirement:
    """Facts Stage 6 must verify before executing one quality strategy."""

    required_codec: NativeCodec | None = None
    required_lossless: bool | None = None
    required_bitrate_kbps: int | None = None


@dataclass(frozen=True, slots=True)
class OutputSpecification:
    """Exact delivery contract represented by a user QualityProfile."""

    codec: NativeCodec | None
    container: NativeContainer | None
    bitrate_kbps: int | None
    lossless: bool
    preserve_source: bool = False


@dataclass(frozen=True, slots=True)
class DownloadPlan:
    """A quality-safe future execution strategy; it never performs media work."""

    track_id: int
    track_source_id: int
    provider: MusicProviderName
    provider_track_id: str
    requested_profile: QualityProfile
    source_expectation: SourceMediaRequirement
    output_specification: OutputSpecification
    operation: DownloadPlanOperation
    readiness: DownloadPlanReadiness
    reason: DownloadPlanReason


@dataclass(frozen=True, slots=True)
class QualityProviderDiagnostic:
    track_id: int
    track_source_id: int
    provider: MusicProviderName
    provider_track_id: str
    runtime_status: ProviderRuntimeStatus
    rejection_reason: QualityCandidateRejectionReason | None = None
    provider_error_code: str | None = None


@dataclass(frozen=True, slots=True)
class QualityResolutionResult:
    track_id: int
    requested_profile: QualityProfile
    status: QualityResolutionStatus
    plans: tuple[DownloadPlan, ...]
    provider_diagnostics: tuple[QualityProviderDiagnostic, ...]
    resolved_at: datetime

    @property
    def primary_plan(self) -> DownloadPlan | None:
        return self.plans[0] if self.plans else None

    @property
    def fallback_plans(self) -> tuple[DownloadPlan, ...]:
        return self.plans[1:]


@dataclass(frozen=True, slots=True)
class PreparedSourceMedia:
    """Normalized facts about provider-native media owned by one Stage 6 job."""

    provider: MusicProviderName
    provider_track_id: str
    codec: NativeCodec | None = None
    container: NativeContainer | None = None
    bitrate_kbps: int | None = None
    sample_rate_hz: int | None = None
    bit_depth: int | None = None
    channels: int | None = None
    duration_ms: int | None = None
    lossless: bool | None = None
    file_path: Path | None = None
    native_encoded: bool = True
    provider_decrypted: bool = False
    upstream_quality_transcoded: bool = False
    validation_confidence: SourceValidationConfidence | None = None


@dataclass(frozen=True, slots=True)
class DownloadAttempt:
    provider: MusicProviderName
    track_source_id: int
    plan_rank: int
    status: DownloadAttemptStatus
    failure_code: DownloadFailureCode | None = None


@dataclass(frozen=True, slots=True)
class DownloadResult:
    job_id: str
    track_id: int
    requested_profile: QualityProfile
    track_source_id: int
    provider: MusicProviderName
    provider_track_id: str
    operation: DownloadPlanOperation
    plan_readiness: DownloadPlanReadiness
    source_media: PreparedSourceMedia
    output_media: PreparedSourceMedia
    file_path: Path
    file_size: int
    transcoded: bool
    fallback_index: int
    attempts: tuple[DownloadAttempt, ...]
    created_at: datetime
    encoder: str | None = None


@dataclass(frozen=True, slots=True)
class DownloadJobView:
    id: int
    track_id: int
    quality_profile: QualityProfile
    status: QueueJobStatus
    attempt_count: int
    queued_at: datetime
    available_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_error_code: str | None
    cancel_requested: bool
    artifact_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class UploadJobView:
    id: int
    download_job_id: int
    track_id: int
    quality_profile: QualityProfile
    status: QueueJobStatus
    attempt_count: int
    queued_at: datetime
    available_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_error_code: str | None
    cancel_requested: bool
    artifact_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class UploadRequest:
    upload_job_id: int
    download_job_id: int
    track_id: int
    quality_profile: QualityProfile
    artifact_job_id: str
    artifact_path: Path
    artifact: DownloadArtifactMetadata | None = None
    artifact_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class UploadResult:
    external_id: str | None = None


@dataclass(frozen=True, slots=True)
class DownloadArtifactMetadata:
    """Validated Stage 6 facts durably carried by an UploadJob."""

    track_source_id: int | None
    source_provider: MusicProviderName
    source_provider_track_id: str
    operation: DownloadPlanOperation
    transcoded: bool
    source_codec: NativeCodec | None
    source_container: NativeContainer | None
    source_bitrate_kbps: int | None
    output_codec: NativeCodec | None
    output_container: NativeContainer | None
    output_bitrate_kbps: int | None
    sample_rate_hz: int | None
    bit_depth: int | None
    channels: int | None
    duration_ms: int | None
    file_size_bytes: int
    encoder: str | None = None


@dataclass(frozen=True, slots=True)
class CachedTelegramFile:
    cache_id: int
    telegram_bot_id: int
    track_id: int
    quality_profile: QualityProfile
    artifact_fingerprint: str | None
    file_id: str
    file_unique_id: str
    media_kind: TelegramMediaKind
    cache_chat_id: int
    cache_message_id: int
    file_size_bytes: int | None
    source_track_source_id: int | None
    source_provider: MusicProviderName
    source_provider_track_id: str
    operation: DownloadPlanOperation
    transcoded: bool
    source_codec: NativeCodec | None
    source_container: NativeContainer | None
    source_bitrate_kbps: int | None
    output_codec: NativeCodec | None
    output_container: NativeContainer | None
    output_bitrate_kbps: int | None
    sample_rate_hz: int | None
    bit_depth: int | None
    channels: int | None
    duration_ms: int | None
    encoder: str | None
    status: TelegramCacheStatus
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None
    invalidated_at: datetime | None
    invalid_reason_code: str | None


@dataclass(frozen=True, slots=True)
class TelegramCacheStats:
    active_entries: int
    invalid_entries: int
    recorded_media_bytes: int


@dataclass(frozen=True, slots=True)
class TelegramBotIdentity:
    telegram_bot_id: int
    username: str | None = None


@dataclass(frozen=True, slots=True)
class TelegramUploadReceipt:
    telegram_bot_id: int
    chat_id: int
    message_id: int
    media_kind: TelegramMediaKind
    file_id: str
    file_unique_id: str
    file_size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class DeliveryPreparationResult:
    status: DeliveryPreparationStatus
    track_id: int
    quality_profile: QualityProfile
    cached_file: CachedTelegramFile | None = None
    subscriber: JobSubscriberView | None = None
    download_job_id: int | None = None

    def __post_init__(self) -> None:
        cache_hit = self.status is DeliveryPreparationStatus.CACHE_HIT
        if cache_hit != (self.cached_file is not None):
            raise ValueError("CACHE_HIT requires exactly one cached file")
        if cache_hit and (self.subscriber is not None or self.download_job_id is not None):
            raise ValueError("CACHE_HIT cannot contain pending work")
        if not cache_hit and (self.subscriber is None or self.download_job_id is None):
            raise ValueError("PENDING requires subscriber and download job")


@dataclass(frozen=True, slots=True)
class QueueStatusCounts:
    queued: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0


@dataclass(frozen=True, slots=True)
class WorkerSettingValues:
    current: int
    default: int
    maximum: int


@dataclass(frozen=True, slots=True)
class WorkerSettingsSnapshot:
    download: WorkerSettingValues
    upload: WorkerSettingValues


@dataclass(frozen=True, slots=True)
class WorkerPoolSnapshot:
    desired_workers: int
    actual_workers: int
    default_workers: int
    max_workers: int


@dataclass(frozen=True, slots=True)
class QueueRuntimeSnapshot:
    download: WorkerPoolSnapshot
    upload: WorkerPoolSnapshot
    download_jobs: QueueStatusCounts
    upload_jobs: QueueStatusCounts
    singleflight: SingleFlightSnapshot | None = None


@dataclass(frozen=True, slots=True)
class JobSubscriberView:
    id: str
    download_job_id: int
    track_id: int
    quality_profile: QualityProfile
    status: SubscriberStatus
    request_key: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    last_error_code: str | None


@dataclass(frozen=True, slots=True)
class SingleFlightSubmission:
    subscriber: JobSubscriberView
    download_job: DownloadJobView
    created_new_job: bool
    joined_existing_flight: bool
    returned_existing_subscriber: bool


@dataclass(frozen=True, slots=True)
class SubscriberStatusCounts:
    waiting: int = 0
    ready: int = 0
    failed: int = 0
    cancelled: int = 0


@dataclass(frozen=True, slots=True)
class SingleFlightSnapshot:
    active_flights: int
    subscribers: SubscriberStatusCounts


@dataclass(frozen=True, slots=True)
class NormalizedTrackMetadata:
    provider: MusicProviderName
    provider_track_id: str
    source_url: str | None
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
class AlbumTrackSnapshot:
    provider_track_id: str
    position: int
    title: str | None = None
    artist: str | None = None
    disc_number: int | None = None
    track_number: int | None = None
    duration_ms: int | None = None
    explicit: bool | None = None


@dataclass(frozen=True, slots=True)
class AlbumSnapshot:
    provider: MusicProviderName
    provider_album_id: str
    source_url: str
    title: str
    artist: str
    tracks: tuple[AlbumTrackSnapshot, ...]
    release_date: str | None = None
    duration_ms: int | None = None

    @property
    def track_count(self) -> int:
        return len(self.tracks)


@dataclass(frozen=True, slots=True)
class ResolvedCollectionItem:
    """Provider-neutral immutable member of an album or playlist snapshot."""

    position: int
    provider_media_id: str
    title: str | None = None
    artist: str | None = None
    duration_ms: int | None = None
    source_reference: str | None = None

    def __post_init__(self) -> None:
        if self.position < 1 or not self.provider_media_id.strip():
            raise ValueError("collection item position and media ID are required")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("collection item duration must not be negative")


@dataclass(frozen=True, slots=True)
class ResolvedCollection:
    """Immutable provider collection membership returned before persistence."""

    source_type: BatchSourceType
    provider: MusicProviderName
    collection_id: str
    source_reference: str
    title: str
    creator: str | None
    items: tuple[ResolvedCollectionItem, ...]

    def __post_init__(self) -> None:
        if (
            not self.collection_id.strip()
            or not self.source_reference.strip()
            or not self.title.strip()
        ):
            raise ValueError("collection identity and title are required")
        if not self.items:
            raise ValueError("collection must contain at least one item")
        positions = [item.position for item in self.items]
        if positions != list(range(1, len(positions) + 1)):
            raise ValueError("collection item positions must be contiguous and ordered")


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
