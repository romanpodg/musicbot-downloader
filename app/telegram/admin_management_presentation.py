"""Localized presentation and strict callback codec for administrator management."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.i18n import LocalizationService
from app.services.admin_management import (
    AdministratorPage,
    ManagedUser,
    PromotionCandidatePage,
)

MAX_ADMIN_CALLBACK_PAGE = 1_000_000
MAX_INTERNAL_USER_ID = 9_223_372_036_854_775_807


class AdminManagementCallbackAction(StrEnum):
    LIST = "l"
    CANDIDATES = "a"
    ADMINISTRATOR = "u"
    PROMOTE_CONFIRMATION = "p"
    PROMOTE_COMMIT = "pc"
    REMOVE_CONFIRMATION = "r"
    REMOVE_COMMIT = "rc"


@dataclass(frozen=True, slots=True)
class AdminManagementCallback:
    action: AdminManagementCallbackAction
    page: int
    target_user_id: int | None = None


def encode_admin_management_callback(
    action: AdminManagementCallbackAction,
    *,
    page: int = 0,
    target_user_id: int | None = None,
) -> str:
    if not 0 <= page <= MAX_ADMIN_CALLBACK_PAGE:
        raise ValueError("invalid administrator-management page")
    if action in {
        AdminManagementCallbackAction.LIST,
        AdminManagementCallbackAction.CANDIDATES,
    }:
        if target_user_id is not None:
            raise ValueError("target is not allowed for this action")
        value = f"adm2:{action.value}:{page}"
    else:
        if target_user_id is None or not 0 < target_user_id <= MAX_INTERNAL_USER_ID:
            raise ValueError("invalid administrator-management target")
        value = f"adm2:{action.value}:{target_user_id}:{page}"
    if len(value.encode()) > 64:
        raise ValueError("administrator-management callback exceeds Telegram limit")
    return value


def parse_admin_management_callback(value: str | None) -> AdminManagementCallback | None:
    if not value:
        return None
    parts = value.split(":")
    if len(parts) not in {3, 4} or parts[0] != "adm2":
        return None
    try:
        action = AdminManagementCallbackAction(parts[1])
    except ValueError:
        return None
    if action in {
        AdminManagementCallbackAction.LIST,
        AdminManagementCallbackAction.CANDIDATES,
    }:
        if len(parts) != 3:
            return None
        page = _bounded_int(parts[2], minimum=0, maximum=MAX_ADMIN_CALLBACK_PAGE)
        return AdminManagementCallback(action, page) if page is not None else None
    if len(parts) != 4:
        return None
    target = _bounded_int(parts[2], minimum=1, maximum=MAX_INTERNAL_USER_ID)
    page = _bounded_int(parts[3], minimum=0, maximum=MAX_ADMIN_CALLBACK_PAGE)
    if target is None or page is None:
        return None
    return AdminManagementCallback(action, page, target)


class AdminManagementPresentation:
    def __init__(self, i18n: LocalizationService) -> None:
        self.i18n = i18n

    def text(self, key: str, locale: str, **values: object) -> str:
        return self.i18n.translate(key, locale, **values)

    def administrators_text(self, result: AdministratorPage, locale: str) -> str:
        sections = [
            self.text("admin.management_title", locale),
            "\n".join(
                (
                    self.text("admin.management_owner", locale),
                    self.identity_text(result.owner, locale),
                )
            ),
            self.text("admin.management_count", locale, count=result.total_count),
        ]
        if result.total_pages > 1:
            sections.append(
                self.text(
                    "admin.management_page",
                    locale,
                    page=result.page + 1,
                    pages=result.total_pages,
                )
            )
        if not result.administrators:
            sections.append(self.text("admin.management_empty", locale))
        return _telegram_text(sections)

    def candidates_text(self, result: PromotionCandidatePage, locale: str) -> str:
        sections = [
            self.text("admin.candidate_title", locale),
            self.text("admin.candidate_count", locale, count=result.total_count),
        ]
        if result.total_pages > 1:
            sections.append(
                self.text(
                    "admin.management_page",
                    locale,
                    page=result.page + 1,
                    pages=result.total_pages,
                )
            )
        if not result.candidates:
            sections.extend(
                (
                    self.text("admin.candidate_empty", locale),
                    self.text("admin.candidate_interaction_required", locale),
                )
            )
        return _telegram_text(sections)

    def administrator_text(self, user: ManagedUser, locale: str) -> str:
        return _telegram_text(
            [self.text("admin.administrator_title", locale), self.identity_text(user, locale)]
        )

    def promotion_confirmation_text(self, user: ManagedUser, locale: str) -> str:
        return _telegram_text(
            [
                self.text("admin.promote_title", locale),
                self.identity_text(user, locale),
                self.text("admin.promote_effect", locale),
            ]
        )

    def removal_confirmation_text(self, user: ManagedUser, locale: str) -> str:
        return _telegram_text(
            [
                self.text("admin.remove_title", locale),
                self.identity_text(user, locale),
                self.text("admin.remove_effect", locale),
            ]
        )

    def identity_text(self, user: ManagedUser, locale: str) -> str:
        telegram_id = self.text("admin.identity_telegram_id", locale, telegram_id=user.telegram_id)
        username = _safe_username(user.username)
        return f"@{username}\n{telegram_id}" if username else telegram_id

    def administrators_keyboard(
        self, result: AdministratorPage, locale: str
    ) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton(
                    text=_identity_button(user),
                    callback_data=encode_admin_management_callback(
                        AdminManagementCallbackAction.ADMINISTRATOR,
                        target_user_id=user.id,
                        page=result.page,
                    ),
                )
            ]
            for user in result.administrators
        ]
        rows.append(
            [
                InlineKeyboardButton(
                    text=self.text("admin.management_add", locale),
                    callback_data=encode_admin_management_callback(
                        AdminManagementCallbackAction.CANDIDATES, page=0
                    ),
                )
            ]
        )
        rows.extend(
            self._pagination(
                AdminManagementCallbackAction.LIST, result.page, result.total_pages, locale
            )
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=self.text("admin.management_refresh", locale),
                    callback_data=encode_admin_management_callback(
                        AdminManagementCallbackAction.LIST, page=result.page
                    ),
                ),
                InlineKeyboardButton(
                    text=self.text("admin.management_back", locale),
                    callback_data="adm1:refresh",
                ),
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def candidates_keyboard(
        self, result: PromotionCandidatePage, locale: str
    ) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton(
                    text=_identity_button(user),
                    callback_data=encode_admin_management_callback(
                        AdminManagementCallbackAction.PROMOTE_CONFIRMATION,
                        target_user_id=user.id,
                        page=result.page,
                    ),
                )
            ]
            for user in result.candidates
        ]
        rows.extend(
            self._pagination(
                AdminManagementCallbackAction.CANDIDATES,
                result.page,
                result.total_pages,
                locale,
            )
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=self.text("admin.management_back", locale),
                    callback_data=encode_admin_management_callback(
                        AdminManagementCallbackAction.LIST, page=0
                    ),
                )
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def administrator_keyboard(
        self, user: ManagedUser, *, page: int, locale: str
    ) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=self.text("admin.remove_button", locale),
                        callback_data=encode_admin_management_callback(
                            AdminManagementCallbackAction.REMOVE_CONFIRMATION,
                            target_user_id=user.id,
                            page=page,
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=self.text("admin.management_back", locale),
                        callback_data=encode_admin_management_callback(
                            AdminManagementCallbackAction.LIST, page=page
                        ),
                    )
                ],
            ]
        )

    def promotion_confirmation_keyboard(
        self, user: ManagedUser, *, page: int, locale: str
    ) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=self.text("admin.promote_confirm", locale),
                        callback_data=encode_admin_management_callback(
                            AdminManagementCallbackAction.PROMOTE_COMMIT,
                            target_user_id=user.id,
                            page=page,
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=self.text("admin.management_cancel", locale),
                        callback_data=encode_admin_management_callback(
                            AdminManagementCallbackAction.CANDIDATES, page=page
                        ),
                    )
                ],
            ]
        )

    def removal_confirmation_keyboard(
        self, user: ManagedUser, *, page: int, locale: str
    ) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=self.text("admin.remove_confirm", locale),
                        callback_data=encode_admin_management_callback(
                            AdminManagementCallbackAction.REMOVE_COMMIT,
                            target_user_id=user.id,
                            page=page,
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=self.text("admin.management_cancel", locale),
                        callback_data=encode_admin_management_callback(
                            AdminManagementCallbackAction.ADMINISTRATOR,
                            target_user_id=user.id,
                            page=page,
                        ),
                    )
                ],
            ]
        )

    def _pagination(
        self,
        action: AdminManagementCallbackAction,
        page: int,
        total_pages: int,
        locale: str,
    ) -> list[list[InlineKeyboardButton]]:
        buttons: list[InlineKeyboardButton] = []
        if page > 0:
            buttons.append(
                InlineKeyboardButton(
                    text=self.text("admin.previous_page", locale),
                    callback_data=encode_admin_management_callback(action, page=page - 1),
                )
            )
        if page + 1 < total_pages:
            buttons.append(
                InlineKeyboardButton(
                    text=self.text("admin.next_page", locale),
                    callback_data=encode_admin_management_callback(action, page=page + 1),
                )
            )
        return [buttons] if buttons else []


def _bounded_int(value: str, *, minimum: int, maximum: int) -> int | None:
    if not value or len(value) > 19 or not value.isascii() or not value.isdecimal():
        return None
    parsed = int(value)
    return parsed if minimum <= parsed <= maximum else None


def _safe_username(value: str | None) -> str | None:
    if not value:
        return None
    safe = "".join(character for character in value if character.isprintable()).strip().lstrip("@")
    return safe[:32] or None


def _identity_button(user: ManagedUser) -> str:
    username = _safe_username(user.username)
    return f"@{username} · {user.telegram_id}" if username else str(user.telegram_id)


def _telegram_text(sections: list[str]) -> str:
    rendered = "\n\n".join(sections)
    if len(rendered) > 4096:
        raise ValueError("administrator-management message exceeds Telegram limit")
    return rendered
