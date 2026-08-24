"""Reusable Stage 14 Telegram keyboard factory."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.application.ux.flows.navigation import UxMenu
from app.i18n import LocalizationService
from app.telegram.callbacks import encode_ux_callback


class UxKeyboardFactory:
    def __init__(self, i18n: LocalizationService) -> None:
        self._i18n = i18n

    def main_menu(self, locale: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [self._button("ux.button.search", locale, "menu", "section", UxMenu.SEARCH)],
                [self._button("ux.button.account", locale, "menu", "section", UxMenu.ACCOUNT)],
                [self._button("ux.button.providers", locale, "menu", "section", UxMenu.PROVIDERS)],
                [self._button("ux.button.settings", locale, "menu", "section", UxMenu.SETTINGS)],
            ]
        )

    def navigation(self, locale: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [self._button("ux.button.back", locale, "menu", "open")],
            ]
        )

    def confirmation(self, locale: str, *, confirm: str, cancel: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    self._button("ux.button.confirm", locale, "operation", "confirm", confirm),
                    self._button("ux.button.cancel", locale, "operation", "cancel", cancel),
                ]
            ]
        )

    def cancel(self, locale: str, *, operation: str = "current") -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [self._button("ux.button.cancel", locale, "operation", "cancel", operation)],
            ]
        )

    def for_menu(self, locale: str, menu: UxMenu | None) -> InlineKeyboardMarkup | None:
        if menu is UxMenu.MAIN:
            return self.main_menu(locale)
        if menu is not None:
            return self.navigation(locale)
        return None

    def _button(
        self,
        key: str,
        locale: str,
        action: str,
        entity: str,
        identifier: UxMenu | str | None = None,
    ) -> InlineKeyboardButton:
        value = identifier.value if isinstance(identifier, UxMenu) else identifier
        return InlineKeyboardButton(
            text=self._i18n.translate(key, locale),
            callback_data=encode_ux_callback(action, entity, value),
        )
