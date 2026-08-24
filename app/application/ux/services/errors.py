"""Safe UX error normalization; never return exception text to presentation."""

from __future__ import annotations

from enum import StrEnum

from app.core.exceptions import InvalidTrackUrl, UnsupportedMediaType, UnsupportedProvider


class UxErrorMessage(StrEnum):
    INVALID_REQUEST = "error.invalid_request"
    OPERATION_FAILED = "error.operation_failed"


class UxErrorService:
    def message_name(self, error: Exception) -> UxErrorMessage:
        if isinstance(
            error, (ValueError, InvalidTrackUrl, UnsupportedMediaType, UnsupportedProvider)
        ):
            return UxErrorMessage.INVALID_REQUEST
        return UxErrorMessage.OPERATION_FAILED
