"""Small, transport-neutral foundation for a user's current UX state."""

from __future__ import annotations

from enum import StrEnum

from app.core.telegram_context import TelegramChatType, TelegramContext


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
    UxState.IDLE: frozenset(
        (UxState.IDLE, UxState.MENU, UxState.SEARCH_INPUT, UxState.SEARCHING, UxState.ERROR)
    ),
    UxState.MENU: frozenset(
        (
            UxState.MENU,
            UxState.IDLE,
            UxState.SEARCH_INPUT,
            UxState.SEARCHING,
            UxState.PROCESSING,
            UxState.ERROR,
        )
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
        (
            UxState.SEARCH_INPUT,
            UxState.SEARCHING,
            UxState.MENU,
            UxState.IDLE,
            UxState.ERROR,
        )
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
        self._states: dict[TelegramContext, UxState] = {}

    def current(self, context: TelegramContext | int) -> UxState:
        """Return state for one conversation context.

        The integer form is retained only for Stage 14 callers that predate chat
        context support; production Telegram paths always pass ``TelegramContext``.
        """

        key = _coerce_context(context)
        return self._states.get(key, UxState.IDLE)

    def transition(self, context: TelegramContext | int, target: UxState) -> UxState:
        key = _coerce_context(context)
        current = self.current(key)
        if target not in _TRANSITIONS[current]:
            raise ValueError(f"invalid UX transition: {current} -> {target}")
        self._states[key] = target
        return target


def _coerce_context(context: TelegramContext | int) -> TelegramContext:
    if isinstance(context, TelegramContext):
        return context
    if isinstance(context, int):
        if context <= 0:
            raise ValueError("user ID must be positive")
        # Compatibility for pre-Stage-19 application callers. This key is never
        # used by Telegram handlers, which pass the exact incoming context.
        return TelegramContext(context, context, TelegramChatType.PRIVATE)
    raise TypeError("UX state key must be TelegramContext")
