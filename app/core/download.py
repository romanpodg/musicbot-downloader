"""Transport-neutral Stage 18 download intent and lifecycle contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.core.enums import QualityProfile
from app.core.search import Track


@dataclass(frozen=True, slots=True)
class DownloadOptions:
    """Optional user choices; this value never performs media or transport work."""

    quality_profile: QualityProfile | None = None

    def __post_init__(self) -> None:
        if self.quality_profile is not None and not isinstance(
            self.quality_profile, QualityProfile
        ):
            raise ValueError("invalid download quality profile")


@dataclass(frozen=True, slots=True)
class DownloadRequest:
    """One user's confirmed intent to download a normalized Stage 17 catalog track."""

    user_id: int
    recognized_track: Track
    options: DownloadOptions = DownloadOptions()

    def __post_init__(self) -> None:
        if self.user_id <= 0:
            raise ValueError("download user ID must be positive")
        if not isinstance(self.recognized_track, Track):
            raise TypeError("download request requires a recognized search Track")
        if not isinstance(self.options, DownloadOptions):
            raise TypeError("download request options must be DownloadOptions")


@dataclass(frozen=True, slots=True)
class CancelDownloadRequest:
    """Prepared cancellation intent; Stage 18 does not change worker cancellation semantics."""

    user_id: int
    download_job_id: int | None = None
    delivery_request_id: int | None = None

    def __post_init__(self) -> None:
        if self.user_id <= 0:
            raise ValueError("download user ID must be positive")
        if self.download_job_id is None and self.delivery_request_id is None:
            raise ValueError("cancellation requires a download job or delivery request ID")
        for value in (self.download_job_id, self.delivery_request_id):
            if value is not None and value <= 0:
                raise ValueError("cancellation identifier must be positive")


@dataclass(frozen=True, slots=True)
class DownloadDeliveryTarget:
    """Opaque delivery destination data owned by the delivery adapter, not the use case."""

    user_id: int
    destination_id: int
    source_message_id: int

    def __post_init__(self) -> None:
        if self.user_id <= 0:
            raise ValueError("delivery user ID must be positive")
        if self.destination_id == 0:
            raise ValueError("delivery destination ID must not be zero")
        if self.source_message_id <= 0:
            raise ValueError("delivery source message ID must be positive")


class DownloadSubmissionState(StrEnum):
    AWAITING_QUALITY = "AWAITING_QUALITY"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class DownloadSubmission:
    """Safe, application-facing result of admitting a confirmed download intent."""

    request: DownloadRequest
    canonical_track_id: int
    delivery_request_id: int
    state: DownloadSubmissionState
    download_job_id: int | None = None

    def __post_init__(self) -> None:
        if self.canonical_track_id <= 0:
            raise ValueError("canonical track ID must be positive")
        if self.delivery_request_id <= 0:
            raise ValueError("delivery request ID must be positive")
        if self.download_job_id is not None and self.download_job_id <= 0:
            raise ValueError("download job ID must be positive")
