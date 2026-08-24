"""Safe UX error normalization; never return exception text to presentation."""

from __future__ import annotations

from enum import StrEnum

from app.core.exceptions import InvalidTrackUrl, UnsupportedMediaType, UnsupportedProvider
from app.services.track_search import TrackSearchUnavailable


class UxErrorMessage(StrEnum):
    INVALID_REQUEST = "error.invalid_request"
    SEARCH_UNAVAILABLE = "error.search_unavailable"
    OPERATION_FAILED = "error.operation_failed"


class UxErrorService:
    def message_name(self, error: Exception) -> UxErrorMessage:
        if isinstance(error, TrackSearchUnavailable):
            return UxErrorMessage.SEARCH_UNAVAILABLE
        if isinstance(
            error, (ValueError, InvalidTrackUrl, UnsupportedMediaType, UnsupportedProvider)
        ):
            return UxErrorMessage.INVALID_REQUEST
        return UxErrorMessage.OPERATION_FAILED
