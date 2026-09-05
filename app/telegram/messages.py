"""Central Stage 14 user-facing message facade, ready for localization growth."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from app.i18n import LocalizationService


class UxMessage(StrEnum):
    WELCOME = "welcome"
    HELP = "help"
    MENU_MAIN = "menu.main"
    MENU_SEARCH = "menu.search"
    MENU_ACCOUNT = "menu.account"
    MENU_PROVIDERS = "menu.providers"
    MENU_SETTINGS = "menu.settings"
    SEARCH_PROMPT = "search.prompt"
    SEARCH_RESULTS = "search.results"
    SEARCH_NO_MATCH = "search.no_match"
    DOWNLOAD_CONFIRMATION = "download.confirmation"
    DOWNLOAD_ALTERNATIVES = "download.alternatives"
    DOWNLOAD_QUEUED = "download.queued"
    DOWNLOAD_CANCELLED = "download.cancelled"
    DOWNLOAD_FAILED = "error.download_failed"
    INVALID_SELECTION = "error.invalid_selection"
    INVALID_REQUEST = "error.invalid_request"
    SEARCH_UNAVAILABLE = "error.search_unavailable"
    OPERATION_FAILED = "error.operation_failed"
    PRIVATE_ONLY = "system.private_only"
    CHAT_ACCESS_DENIED = "system.chat_access_denied"


_KEYS = {message.value: f"ux.{message.value}" for message in UxMessage}


class UxMessageService:
    """Maps stable UX names to localized catalog keys without handler literals."""

    def __init__(self, i18n: LocalizationService) -> None:
        self._i18n = i18n

    @property
    def default_locale(self) -> str:
        return self._i18n.default_locale

    def get(self, message: UxMessage | str, locale: str, **values: Any) -> str:
        name = message.value if isinstance(message, UxMessage) else message
        try:
            key = _KEYS[name]
        except KeyError as exc:
            raise ValueError(f"unknown UX message: {name}") from exc
        return self._i18n.translate(key, locale, **values)
