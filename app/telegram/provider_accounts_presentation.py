"""Telegram rendering for sanitized provider-account state."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.enums import MusicProviderName
from app.core.provider_accounts import (
    ProviderAccountOverview,
    ProviderAccountState,
    ProviderAccountStatus,
    ProviderAuthorizationChallenge,
    ProviderAuthorizationMethod,
    ProviderAuthorizationOutcome,
    ProviderAuthorizationOutcomeStatus,
)
from app.i18n import LocalizationService


class ProviderAccountsCallbackAction(StrEnum):
    OPEN = "o"
    DETAIL = "d"
    REFRESH = "r"
    BACK = "b"
    CONNECT = "c"
    CANCEL = "x"


@dataclass(frozen=True, slots=True)
class ProviderAccountsCallback:
    action: ProviderAccountsCallbackAction
    provider: MusicProviderName | None = None
    flow_id: str | None = None


_FLOW_ID = re.compile(r"^[0-9a-f]{16}$")


def encode_provider_accounts_callback(
    action: ProviderAccountsCallbackAction,
    provider: MusicProviderName | None = None,
    flow_id: str | None = None,
) -> str:
    value = f"adm5:{action.value}"
    if provider is not None:
        value = f"{value}:{provider.value}"
    if flow_id is not None:
        if _FLOW_ID.fullmatch(flow_id) is None:
            raise ValueError("invalid provider authorization flow id")
        value = f"{value}:{flow_id}"
    if len(value.encode("utf-8")) > 64:
        raise ValueError("provider-account callback exceeds Telegram limit")
    return value


def parse_provider_accounts_callback(value: str | None) -> ProviderAccountsCallback | None:
    if value is None:
        return None
    parts = value.split(":")
    if len(parts) not in {2, 3, 4} or parts[0] != "adm5":
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
    if len(parts) == 4:
        if action is not ProviderAccountsCallbackAction.CANCEL:
            return None
        try:
            provider = MusicProviderName(parts[2])
        except ValueError:
            return None
        flow_id = parts[3]
        if _FLOW_ID.fullmatch(flow_id) is None:
            return None
        return ProviderAccountsCallback(action, provider, flow_id)
    if action not in {
        ProviderAccountsCallbackAction.DETAIL,
        ProviderAccountsCallbackAction.REFRESH,
        ProviderAccountsCallbackAction.CONNECT,
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
        ]
        lines.extend(("", self.text(self._detail_hint_key(status), locale)))
        return _bounded("\n".join(lines))

    def authorization_text(self, challenge: ProviderAuthorizationChallenge, locale: str) -> str:
        return _bounded(
            "\n\n".join(
                (
                    self.text("admin.tidal_auth_title", locale),
                    self.text("admin.tidal_auth_instructions", locale),
                    self.text(
                        "admin.tidal_auth_expires",
                        locale,
                        time=challenge.expires_at.strftime("%H:%M:%S UTC"),
                    ),
                    self.text("admin.tidal_auth_progress", locale),
                )
            )
        )

    def authorization_result_text(
        self,
        status: ProviderAccountStatus,
        outcome: ProviderAuthorizationOutcome,
        locale: str,
    ) -> str:
        if outcome.status is ProviderAuthorizationOutcomeStatus.READY:
            suffix = self.text("admin.tidal_auth_ready", locale)
        elif outcome.status is ProviderAuthorizationOutcomeStatus.CANCELLED:
            suffix = self.text("admin.tidal_auth_cancelled", locale)
        elif outcome.error_code is not None:
            suffix = self.text(
                f"admin.provider_auth_error.{outcome.error_code.value.lower()}", locale
            )
        else:
            suffix = self.text("admin.tidal_auth_failed", locale)
        return _bounded(f"{self.detail_text(status, locale)}\n\n{suffix}")

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
        rows: list[list[InlineKeyboardButton]] = []
        if (
            status.state
            in {
                ProviderAccountState.NOT_CONFIGURED,
                ProviderAccountState.AUTH_REQUIRED,
                ProviderAccountState.ERROR,
            }
            and ProviderAuthorizationMethod.BROWSER_DEVICE_LINK in status.authorization_methods
        ):
            rows.append(
                [
                    InlineKeyboardButton(
                        text=self.text("admin.tidal_connect", locale),
                        callback_data=encode_provider_accounts_callback(
                            ProviderAccountsCallbackAction.CONNECT, status.provider
                        ),
                    )
                ]
            )
        rows.extend(
            [
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
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def authorization_keyboard(
        self, challenge: ProviderAuthorizationChallenge, locale: str
    ) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=self.text("admin.tidal_open", locale),
                        url=challenge.verification_url,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=self.text("admin.tidal_cancel", locale),
                        callback_data=encode_provider_accounts_callback(
                            ProviderAccountsCallbackAction.CANCEL,
                            challenge.provider,
                            challenge.flow_id,
                        ),
                    )
                ],
            ]
        )

    def _detail_hint_key(self, status: ProviderAccountStatus) -> str:
        if status.state is ProviderAccountState.READY:
            return "admin.provider_accounts_runtime_ready"
        if ProviderAuthorizationMethod.BROWSER_DEVICE_LINK in status.authorization_methods:
            return "admin.provider_accounts_tidal_auth_hint"
        return "admin.provider_accounts_no_auth_flows"

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
