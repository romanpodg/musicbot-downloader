"""Small, reusable Stage 28 Telegram presentation components.

These components intentionally accept durable snapshots rather than services or
Telegram objects.  That keeps provider selection, retries, and persistence out
of handlers while making the visible vocabulary consistent.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.core.enums import DownloadFailureCode, DownloadJobStatus, DownloadPhase
from app.services.download_lifecycle import is_retryable


class UserDownloadState(StrEnum):
    PREPARING = "PREPARING"
    WAITING = "WAITING"
    DOWNLOADING = "DOWNLOADING"
    PROCESSING = "PROCESSING"
    SENDING = "SENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class DownloadStatusView:
    state: UserDownloadState
    label: str
    terminal: bool
    retryable: bool = False


class DownloadStatusPresenter:
    """Maps the durable lifecycle to the deliberately small user state model."""

    def present(
        self,
        status: DownloadJobStatus,
        phase: DownloadPhase | None = None,
        failure_code: DownloadFailureCode | None = None,
        *,
        cache_hit: bool = False,
    ) -> DownloadStatusView:
        if status is DownloadJobStatus.SUCCEEDED:
            return DownloadStatusView(UserDownloadState.DELIVERED, "Delivered", True)
        if status is DownloadJobStatus.CANCELLED:
            return DownloadStatusView(UserDownloadState.CANCELLED, "Download cancelled", True)
        if status is DownloadJobStatus.FAILED:
            return DownloadStatusView(
                UserDownloadState.FAILED,
                UserFacingErrorPresenter().message_for(failure_code),
                True,
                failure_code is not None and is_retryable(failure_code),
            )
        if status is DownloadJobStatus.DELIVERING:
            return DownloadStatusView(UserDownloadState.SENDING, "Sending…", False)
        if status is DownloadJobStatus.RETRY_WAIT:
            return DownloadStatusView(UserDownloadState.WAITING, "Waiting to retry…", False)
        if status is DownloadJobStatus.RUNNING:
            if phase is DownloadPhase.PROCESSING:
                return DownloadStatusView(UserDownloadState.PROCESSING, "Processing…", False)
            if phase is DownloadPhase.DOWNLOADING:
                return DownloadStatusView(UserDownloadState.DOWNLOADING, "Downloading…", False)
            return DownloadStatusView(
                UserDownloadState.PREPARING,
                "Preparing…" if cache_hit else "Finding an available source…",
                False,
            )
        if status is DownloadJobStatus.QUEUED:
            return DownloadStatusView(UserDownloadState.WAITING, "Waiting…", False)
        return DownloadStatusView(UserDownloadState.PREPARING, "Preparing…", False)


class UserFacingErrorPresenter:
    """Safe failure copy.  Raw exception messages never cross this boundary."""

    def message_for(self, code: DownloadFailureCode | None) -> str:
        if code in {DownloadFailureCode.MEDIA_NOT_FOUND, DownloadFailureCode.MEDIA_UNAVAILABLE}:
            return "This track is unavailable."
        if code in {
            DownloadFailureCode.PROVIDER_TEMPORARY,
            DownloadFailureCode.PROVIDER_RATE_LIMITED,
            DownloadFailureCode.NETWORK,
            DownloadFailureCode.DOWNLOAD_TIMEOUT,
        }:
            return "The source is temporarily unavailable. Please try again."
        if code in {
            DownloadFailureCode.PROCESSING,
            DownloadFailureCode.MEDIA_PROBE_FAILED,
            DownloadFailureCode.TRANSCODE_FAILED,
            DownloadFailureCode.OUTPUT_VALIDATION_FAILED,
        }:
            return "The downloaded audio could not be processed."
        if code in {DownloadFailureCode.DELIVERY_TEMPORARY, DownloadFailureCode.DELIVERY_PERMANENT}:
            return "Telegram could not deliver this download."
        if code is DownloadFailureCode.TEMP_STORAGE_UNAVAILABLE:
            return "Downloads are temporarily limited by available storage."
        return "This download could not be completed."


class TelegramStatusUpdatePolicy:
    """Coalesce status edits; terminal states always win the throttle window."""

    def __init__(
        self,
        *,
        minimum_interval: timedelta = timedelta(seconds=3),
        clock: Callable[[], datetime],
    ) -> None:
        self._minimum_interval = minimum_interval
        self._clock = clock
        self._last: dict[tuple[int, int], tuple[datetime, DownloadStatusView]] = {}

    def should_emit(self, chat_id: int, message_id: int, view: DownloadStatusView) -> bool:
        key = (chat_id, message_id)
        now = self._clock()
        previous = self._last.get(key)
        if previous is None or view.terminal:
            self._last[key] = (now, view)
            return True
        last_at, prior_view = previous
        if prior_view.state is not view.state and now - last_at >= self._minimum_interval:
            self._last[key] = (now, view)
            return True
        return False
