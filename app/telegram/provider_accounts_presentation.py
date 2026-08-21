"""Telegram rendering for sanitized provider-account state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.enums import MusicProviderName
from app.core.provider_accounts import (
    ProviderAccountOverview,
    ProviderAccountState,
    ProviderAccountStatus,
)
from app.i18n import LocalizationService


class ProviderAccountsCallbackAction(StrEnum):
    OPEN = "o"
    DETAIL = "d"
    REFRESH = "r"
    BACK = "b"


@dataclass(frozen=True, slots=True)
class ProviderAccountsCallback:
    action: ProviderAccountsCallbackAction
    provider: MusicProviderName | None = None


def encode_provider_accounts_callback(
    action: ProviderAccountsCallbackAction,
    provider: MusicProviderName | None = None,
) -> str:
    value = f"adm5:{action.value}"
    if provider is not None:
        value = f"{value}:{provider.value}"
    if len(value.encode("utf-8")) > 64:
        raise ValueError("provider-account callback exceeds Telegram limit")
    return value


def parse_provider_accounts_callback(value: str | None) -> ProviderAccountsCallback | None:
    if value is None:
        return None
    parts = value.split(":")
    if len(parts) not in {2, 3} or parts[0] != "adm5":
        return None
    try:
        action = ProviderAccountsCallbackAction(parts[1])
    except ValueError:
        return None
    if len(parts) == 2:
        if action not in {
            ProviderAccountsCallbackAction.OPEN,
            ProviderAccountsCallbackAction.REFRESH,
            ProviderAccountsCallbackAction.BACK,
        }:
            return None
        return ProviderAccountsCallback(action)
    if action not in {
        ProviderAccountsCallbackAction.DETAIL,
        ProviderAccountsCallbackAction.REFRESH,
    }:
        return None
    try:
        provider = MusicProviderName(parts[2])
    except ValueError:
        return None
    return ProviderAccountsCallback(action, provider)


class ProviderAccountsPresentation:
    def __init__(self, i18n: LocalizationService) -> None:
        self._i18n = i18n

    def text(self, key: str, locale: str, **values: object) -> str:
        return self._i18n.translate(key, locale, **values)

    def overview_text(self, overview: ProviderAccountOverview, locale: str) -> str:
        lines = [
            self.text("admin.provider_accounts_title", locale),
            self.text(
                "admin.provider_accounts_checked_at",
                locale,
                time=overview.checked_at.strftime("%H:%M:%S UTC"),
            ),
            "",
        ]
        lines.extend(self._status_line(status, locale) for status in overview.accounts)
        lines.extend(("", self.text("admin.provider_accounts_foundation_hint", locale)))
        return _bounded("\n".join(lines))

    def detail_text(self, status: ProviderAccountStatus, locale: str) -> str:
        lines = [
            self.text(
                "admin.provider_account_detail_title",
                locale,
                provider=self.text(f"provider.{status.provider.value}", locale),
            ),
            "",
            self.text(
                "admin.provider_account_state",
                locale,
                status=self.text(_state_key(status.state), locale),
            ),
            self.text(
                "admin.provider_accounts_checked_at",
                locale,
                time=status.checked_at.strftime("%H:%M:%S UTC"),
            ),
            "",
            self.text("admin.provider_accounts_no_auth_flows", locale),
        ]
        return _bounded("\n".join(lines))

    def overview_keyboard(
        self, overview: ProviderAccountOverview, locale: str
    ) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton(
                    text=self.text(f"provider.{status.provider.value}", locale),
                    callback_data=encode_provider_accounts_callback(
                        ProviderAccountsCallbackAction.DETAIL, status.provider
                    ),
                )
            ]
            for status in overview.accounts
        ]
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text=self.text("admin.provider_accounts_refresh", locale),
                        callback_data=encode_provider_accounts_callback(
                            ProviderAccountsCallbackAction.REFRESH
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=self.text("admin.provider_accounts_back", locale),
                        callback_data=encode_provider_accounts_callback(
                            ProviderAccountsCallbackAction.BACK
                        ),
                    )
                ],
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def detail_keyboard(self, status: ProviderAccountStatus, locale: str) -> InlineKeyboardMarkup:
        # Stage 13.1 intentionally has no Connect button: no real driver is composed.
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=self.text("admin.provider_accounts_refresh", locale),
                        callback_data=encode_provider_accounts_callback(
                            ProviderAccountsCallbackAction.REFRESH, status.provider
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=self.text("admin.provider_accounts_back", locale),
                        callback_data=encode_provider_accounts_callback(
                            ProviderAccountsCallbackAction.OPEN
                        ),
                    )
                ],
            ]
        )

    def _status_line(self, status: ProviderAccountStatus, locale: str) -> str:
        return self.text(
            "admin.provider_accounts_entry",
            locale,
            icon=_state_icon(status.state),
            provider=self.text(f"provider.{status.provider.value}", locale),
            status=self.text(_state_key(status.state), locale),
        )


def _state_key(state: ProviderAccountState) -> str:
    return f"admin.provider_accounts_{state.value.lower()}"


def _state_icon(state: ProviderAccountState) -> str:
    return {
        ProviderAccountState.READY: "✅",
        ProviderAccountState.NOT_CONFIGURED: "⚪",
        ProviderAccountState.AUTH_REQUIRED: "🔐",
        ProviderAccountState.AUTHORIZING: "⏳",
        ProviderAccountState.ERROR: "⚠️",
        ProviderAccountState.UNSUPPORTED: "⛔",
    }[state]


def _bounded(value: str) -> str:
    if len(value) > 4096:
        raise ValueError("provider-account message exceeds Telegram limit")
    return value
