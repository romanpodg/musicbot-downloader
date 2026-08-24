"""Thin Stage 14 Telegram adapter for welcome, help, and menu navigation."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from aiogram.types import User as AiogramUser

from app.application.ux.flows.navigation import UxFlowService, UxMenu, UxScreen
from app.application.ux.services.errors import UxErrorService
from app.services.telegram_users import TelegramUserProfile, TelegramUserService
from app.storage.models import User
from app.telegram.callbacks import parse_ux_callback
from app.telegram.keyboards import UxKeyboardFactory
from app.telegram.messages import UxMessage, UxMessageService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UxHandlerDependencies:
    users: TelegramUserService
    flows: UxFlowService
    messages: UxMessageService
    keyboards: UxKeyboardFactory
    errors: UxErrorService


def create_ux_router(dependencies: UxHandlerDependencies) -> Router:
    router = Router(name="stage14-ux")

    @router.message(CommandStart(deep_link=False))
    async def start(message: Message) -> None:
        await _handle_message(message, dependencies, dependencies.flows.start)

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        await _handle_message(message, dependencies, dependencies.flows.help)

    @router.message(Command("menu"))
    async def menu_command(message: Message) -> None:
        await _handle_message(message, dependencies, dependencies.flows.open_menu)

    @router.callback_query(F.data.startswith("ux1:"))
    async def menu_callback(callback: CallbackQuery) -> None:
        parsed = parse_ux_callback(callback.data)
        if parsed is None or parsed.action != "menu":
            await _invalid_callback(callback, dependencies)
            return
        menu = _menu_from_callback(parsed.entity, parsed.identifier)
        if menu is None:
            await _invalid_callback(callback, dependencies)
            return
        try:
            user = await dependencies.users.observe(_profile(callback.from_user))
            if not await _private_callback(callback, user, dependencies):
                return
            screen = await dependencies.flows.open_menu(_profile(callback.from_user), menu)
            await _render_callback(
                callback, screen, dependencies, dependencies.users.locale_for(user)
            )
            await callback.answer()
        except Exception as exc:
            logger.error("Telegram UX navigation failed")
            await _operation_failed_callback(callback, dependencies, exc)

    return router


async def _handle_message(
    message: Message,
    dependencies: UxHandlerDependencies,
    operation: Callable[[TelegramUserProfile], Awaitable[UxScreen]],
) -> None:
    if message.from_user is None:
        return
    try:
        user = await dependencies.users.observe(_profile(message.from_user))
        if not await _private_message(message, user, dependencies):
            return
        screen = await operation(_profile(message.from_user))
        locale = dependencies.users.locale_for(user)
        await message.answer(
            dependencies.messages.get(screen.message_key.removeprefix("ux."), locale),
            reply_markup=dependencies.keyboards.for_menu(locale, screen.menu),
        )
    except Exception as exc:
        logger.error("Telegram UX command failed")
        locale = dependencies.messages.default_locale
        await message.answer(
            dependencies.messages.get(dependencies.errors.message_name(exc).value, locale)
        )


async def _render_callback(
    callback: CallbackQuery,
    screen: UxScreen,
    dependencies: UxHandlerDependencies,
    locale: str,
) -> None:
    if not isinstance(callback.message, Message):
        return
    await callback.message.edit_text(
        dependencies.messages.get(screen.message_key.removeprefix("ux."), locale),
        reply_markup=dependencies.keyboards.for_menu(locale, screen.menu),
    )


async def _invalid_callback(callback: CallbackQuery, dependencies: UxHandlerDependencies) -> None:
    locale = dependencies.messages.default_locale
    try:
        user = await dependencies.users.observe(_profile(callback.from_user))
        locale = dependencies.users.locale_for(user)
    except Exception:
        pass
    await callback.answer(
        dependencies.messages.get(UxMessage.INVALID_SELECTION, locale), show_alert=True
    )


async def _operation_failed_callback(
    callback: CallbackQuery, dependencies: UxHandlerDependencies, error: Exception
) -> None:
    locale = dependencies.messages.default_locale
    try:
        user = await dependencies.users.observe(_profile(callback.from_user))
        locale = dependencies.users.locale_for(user)
    except Exception:
        pass
    await callback.answer(
        dependencies.messages.get(dependencies.errors.message_name(error).value, locale),
        show_alert=True,
    )


def _menu_from_callback(entity: str, identifier: str | None) -> UxMenu | None:
    if entity == "open" and identifier is None:
        return UxMenu.MAIN
    if entity != "section" or identifier is None:
        return None
    try:
        return UxMenu(identifier)
    except ValueError:
        return None


def _profile(user: AiogramUser) -> TelegramUserProfile:
    return TelegramUserProfile(user.id, user.username, user.first_name, user.language_code)


async def _private_message(
    message: Message, user: User, dependencies: UxHandlerDependencies
) -> bool:
    if message.chat.type == "private":
        return True
    locale = dependencies.users.locale_for(user)
    await message.answer(dependencies.messages.get(UxMessage.PRIVATE_ONLY, locale))
    return False


async def _private_callback(
    callback: CallbackQuery, user: User, dependencies: UxHandlerDependencies
) -> bool:
    if isinstance(callback.message, Message) and callback.message.chat.type == "private":
        return True
    locale = dependencies.users.locale_for(user)
    await callback.answer(
        dependencies.messages.get(UxMessage.PRIVATE_ONLY, locale), show_alert=True
    )
    return False
