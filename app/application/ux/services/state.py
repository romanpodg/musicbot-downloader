"""Small, transport-neutral foundation for a user's current UX state."""

from __future__ import annotations

from enum import StrEnum


class UxState(StrEnum):
    IDLE = "IDLE"
    MENU = "MENU"
    SEARCH_INPUT = "SEARCH_INPUT"
    PROCESSING = "PROCESSING"
    ERROR = "ERROR"

    # Search result selection and delivery interactions remain reserved for later stages.
    SEARCHING = "SEARCHING"
    SEARCH_RESULTS = "SEARCH_RESULTS"
    SELECTING_TRACK = "SELECTING_TRACK"
    DOWNLOADING = "DOWNLOADING"
    UPLOADING = "UPLOADING"
    DOWNLOAD_CONFIRMATION = "DOWNLOAD_CONFIRMATION"
    DOWNLOAD_QUEUED = "DOWNLOAD_QUEUED"
    DOWNLOAD_PROCESSING = "DOWNLOAD_PROCESSING"
    DOWNLOAD_COMPLETED = "DOWNLOAD_COMPLETED"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"


_TRANSITIONS: dict[UxState, frozenset[UxState]] = {
    UxState.IDLE: frozenset((UxState.IDLE, UxState.MENU, UxState.SEARCH_INPUT, UxState.ERROR)),
    UxState.MENU: frozenset(
        (UxState.MENU, UxState.IDLE, UxState.SEARCH_INPUT, UxState.PROCESSING, UxState.ERROR)
    ),
    UxState.SEARCH_INPUT: frozenset(
        (UxState.SEARCH_INPUT, UxState.SEARCHING, UxState.MENU, UxState.IDLE, UxState.ERROR)
    ),
    UxState.PROCESSING: frozenset((UxState.IDLE, UxState.MENU, UxState.ERROR)),
    UxState.ERROR: frozenset((UxState.IDLE, UxState.MENU)),
    UxState.SEARCHING: frozenset(
        (
            UxState.SEARCH_RESULTS,
            UxState.SELECTING_TRACK,
            UxState.DOWNLOAD_CONFIRMATION,
            UxState.ERROR,
            UxState.IDLE,
        )
    ),
    UxState.SEARCH_RESULTS: frozenset(
        (UxState.SEARCH_INPUT, UxState.MENU, UxState.IDLE, UxState.ERROR)
    ),
    UxState.SELECTING_TRACK: frozenset((UxState.PROCESSING, UxState.ERROR, UxState.IDLE)),
    UxState.DOWNLOADING: frozenset((UxState.UPLOADING, UxState.ERROR, UxState.IDLE)),
    UxState.UPLOADING: frozenset((UxState.IDLE, UxState.ERROR)),
    UxState.DOWNLOAD_CONFIRMATION: frozenset(
        (UxState.DOWNLOAD_CONFIRMATION, UxState.DOWNLOAD_QUEUED, UxState.IDLE, UxState.ERROR)
    ),
    UxState.DOWNLOAD_QUEUED: frozenset(
        (
            UxState.DOWNLOAD_QUEUED,
            UxState.DOWNLOAD_PROCESSING,
            UxState.DOWNLOAD_COMPLETED,
            UxState.DOWNLOAD_FAILED,
            UxState.IDLE,
        )
    ),
    UxState.DOWNLOAD_PROCESSING: frozenset(
        (
            UxState.DOWNLOAD_PROCESSING,
            UxState.DOWNLOAD_COMPLETED,
            UxState.DOWNLOAD_FAILED,
            UxState.IDLE,
        )
    ),
    UxState.DOWNLOAD_COMPLETED: frozenset((UxState.IDLE, UxState.MENU)),
    UxState.DOWNLOAD_FAILED: frozenset((UxState.IDLE, UxState.MENU)),
}


class UserUxStateService:
    """In-memory navigation state; durable delivery/card state remains Stage 9-owned."""

    def __init__(self) -> None:
        self._states: dict[int, UxState] = {}

    def current(self, user_id: int) -> UxState:
        _validate_user_id(user_id)
        return self._states.get(user_id, UxState.IDLE)

    def transition(self, user_id: int, target: UxState) -> UxState:
        _validate_user_id(user_id)
        current = self.current(user_id)
        if target not in _TRANSITIONS[current]:
            raise ValueError(f"invalid UX transition: {current} -> {target}")
        self._states[user_id] = target
        return target


def _validate_user_id(user_id: int) -> None:
    if user_id <= 0:
        raise ValueError("user ID must be positive")
