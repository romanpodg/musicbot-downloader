"""Small, transport-neutral foundation for a user's current UX state."""

from __future__ import annotations

from enum import StrEnum


class UxState(StrEnum):
    IDLE = "IDLE"
    MENU = "MENU"
    PROCESSING = "PROCESSING"
    ERROR = "ERROR"

    # Reserved for later stages. Declaring them here keeps the state vocabulary stable
    # without implementing search or delivery interactions in Stage 14.
    SEARCHING = "SEARCHING"
    SELECTING_TRACK = "SELECTING_TRACK"
    DOWNLOADING = "DOWNLOADING"
    UPLOADING = "UPLOADING"


_TRANSITIONS: dict[UxState, frozenset[UxState]] = {
    UxState.IDLE: frozenset((UxState.IDLE, UxState.MENU, UxState.ERROR)),
    UxState.MENU: frozenset((UxState.MENU, UxState.IDLE, UxState.PROCESSING, UxState.ERROR)),
    UxState.PROCESSING: frozenset((UxState.IDLE, UxState.MENU, UxState.ERROR)),
    UxState.ERROR: frozenset((UxState.IDLE, UxState.MENU)),
    UxState.SEARCHING: frozenset((UxState.SELECTING_TRACK, UxState.ERROR, UxState.IDLE)),
    UxState.SELECTING_TRACK: frozenset((UxState.PROCESSING, UxState.ERROR, UxState.IDLE)),
    UxState.DOWNLOADING: frozenset((UxState.UPLOADING, UxState.ERROR, UxState.IDLE)),
    UxState.UPLOADING: frozenset((UxState.IDLE, UxState.ERROR)),
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
