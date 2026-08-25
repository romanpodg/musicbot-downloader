"""Safe UX error normalization; never return exception text to presentation."""

from __future__ import annotations

from enum import StrEnum

from app.core.exceptions import (
    DownloadPipelineError,
    InvalidTrackUrl,
    ProviderAuthenticationError,
    ProviderUnavailable,
    QueueServiceError,
    TemporaryStorageUnavailable,
    UnsupportedMediaType,
    UnsupportedProvider,
    UploadRetryableError,
    UploadTerminalError,
)
from app.services.track_search import TrackSearchUnavailable


class UxErrorMessage(StrEnum):
    INVALID_REQUEST = "error.invalid_request"
    SEARCH_UNAVAILABLE = "error.search_unavailable"
    DOWNLOAD_FAILED = "error.download_failed"
    OPERATION_FAILED = "error.operation_failed"


class UxErrorService:
    def message_name(self, error: Exception) -> UxErrorMessage:
        if isinstance(error, TrackSearchUnavailable):
            return UxErrorMessage.SEARCH_UNAVAILABLE
        if isinstance(
            error,
            (
                DownloadPipelineError,
                QueueServiceError,
                TemporaryStorageUnavailable,
                UploadRetryableError,
                UploadTerminalError,
            ),
        ):
            return UxErrorMessage.DOWNLOAD_FAILED
        if isinstance(
            error, (ValueError, InvalidTrackUrl, UnsupportedMediaType, UnsupportedProvider)
        ):
            return UxErrorMessage.INVALID_REQUEST
        return UxErrorMessage.OPERATION_FAILED

    def download_message_name(self, error: Exception) -> UxErrorMessage:
        """Normalize provider-admission failures only at the download UX boundary."""

        if isinstance(error, (ProviderAuthenticationError, ProviderUnavailable)):
            return UxErrorMessage.DOWNLOAD_FAILED
        return self.message_name(error)
