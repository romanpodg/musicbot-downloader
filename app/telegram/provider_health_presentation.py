"""Localized Provider Health snapshot presentation and strict callback codec."""

from __future__ import annotations

from enum import StrEnum

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.enums import MusicProviderName, ProviderHealthStatus
from app.core.models import ProviderHealthSnapshot
from app.i18n import LocalizationService


class ProviderHealthCallbackAction(StrEnum):
    OPEN = "h"
    REFRESH = "r"
    BACK = "b"


def encode_provider_health_callback(action: ProviderHealthCallbackAction) -> str:
    value = f"adm4:{action.value}"
    if len(value.encode("utf-8")) > 64:
        raise ValueError("provider-health callback exceeds Telegram limit")
    return value


def parse_provider_health_callback(value: str | None) -> ProviderHealthCallbackAction | None:
    if not value or len(value.encode("utf-8")) > 64:
        return None
    parts = value.split(":")
    if len(parts) != 2 or parts[0] != "adm4":
        return None
    try:
        return ProviderHealthCallbackAction(parts[1])
    except ValueError:
        return None


class ProviderHealthPresentation:
    def __init__(self, i18n: LocalizationService) -> None:
        self.i18n = i18n

    def text(self, key: str, locale: str, **values: object) -> str:
        return self.i18n.translate(key, locale, **values)

    def checking_text(self, locale: str) -> str:
        return "\n\n".join(
            (
                self.text("admin.provider_health_title", locale),
                self.text("admin.provider_health_checking", locale),
            )
        )

    def snapshot_text(self, snapshot: ProviderHealthSnapshot, locale: str) -> str:
        lines = [
            self.text("admin.provider_health_title", locale),
            self.text(
                "admin.provider_health_checked_at",
                locale,
                time=snapshot.checked_at.strftime("%H:%M:%S UTC"),
            ),
            "",
        ]
        for entry in snapshot.entries:
            lines.append(
                self.text(
                    "admin.provider_health_entry",
                    locale,
                    provider=self.text(_provider_key(entry.provider), locale),
                    icon=_status_icon(entry.status),
                    status=self.text(_status_key(entry.status), locale),
                )
            )
        lines.extend(("", self.text("admin.provider_health_semantic_hint", locale)))
        rendered = "\n".join(lines)
        if len(rendered) > 4096:
            raise ValueError("provider-health message exceeds Telegram limit")
        return rendered

    def keyboard(self, locale: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=self.text("admin.provider_health_refresh", locale),
                        callback_data=encode_provider_health_callback(
                            ProviderHealthCallbackAction.REFRESH
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=self.text("admin.provider_health_back", locale),
                        callback_data=encode_provider_health_callback(
                            ProviderHealthCallbackAction.BACK
                        ),
                    )
                ],
            ]
        )


def _provider_key(provider: MusicProviderName) -> str:
    return f"provider.{provider.value}"


def _status_key(status: ProviderHealthStatus) -> str:
    return f"admin.provider_health_{status.value.lower()}"


def _status_icon(status: ProviderHealthStatus) -> str:
    return {
        ProviderHealthStatus.READY: "✅",
        ProviderHealthStatus.AUTH_REQUIRED: "🔐",
        ProviderHealthStatus.UNAVAILABLE: "⛔",
        ProviderHealthStatus.ERROR: "⚠️",
        ProviderHealthStatus.UNKNOWN: "❔",
    }[status]
