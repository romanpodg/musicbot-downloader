"""Thin aiogram router for the authorized, private-chat admin panel."""

from __future__ import annotations

from dataclasses import dataclass

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.types import User as AiogramUser

from app.services.admin_overview import AdminOverviewError, AdminOverviewService
from app.services.authorization import AuthorizationError
from app.services.telegram_users import TelegramUserProfile, TelegramUserService
from app.storage.models import User
from app.telegram.admin_presentation import (
    AdminCallbackAction,
    AdminPresentation,
    parse_admin_callback,
)


@dataclass(frozen=True, slots=True)
class AdminHandlerDependencies:
    users: TelegramUserService
    admin: AdminOverviewService
    presentation: AdminPresentation


def create_admin_router(dependencies: AdminHandlerDependencies) -> Router:
    router = Router(name="stage10-admin-panel")

    @router.message(Command("admin"))
    async def admin_command(message: Message) -> None:
        user = await _observe_message(message, dependencies.users)
        if user is None:
            return
        locale = dependencies.users.locale_for(user)
        try:
            await dependencies.admin.authorize_view(user.id)
        except AuthorizationError:
            await message.answer(dependencies.presentation.text("admin.access_denied", locale))
            return
        if message.chat.type != ChatType.PRIVATE:
            await message.answer(dependencies.presentation.text("admin.private_chat_only", locale))
            return
        try:
            result = await dependencies.admin.get_overview(user.id)
        except AuthorizationError:
            await message.answer(dependencies.presentation.text("admin.access_denied", locale))
            return
        except AdminOverviewError:
            await message.answer(dependencies.presentation.text("admin.refresh_failed", locale))
            return
        await message.answer(
            dependencies.presentation.overview_text(result, locale),
            reply_markup=dependencies.presentation.keyboard(locale),
        )

    @router.callback_query(F.data.startswith("adm1:"))
    async def admin_callback(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        try:
            await dependencies.admin.authorize_view(user.id)
        except AuthorizationError:
            await _deny_callback(callback, dependencies.presentation, locale)
            return
        if not isinstance(callback.message, Message) or (
            callback.message.chat.type != ChatType.PRIVATE
        ):
            await callback.answer(
                dependencies.presentation.text("admin.private_chat_only", locale), show_alert=True
            )
            return

        action = parse_admin_callback(callback.data)
        if action is AdminCallbackAction.REFRESH:
            try:
                result = await dependencies.admin.get_overview(user.id)
            except AuthorizationError:
                await _deny_callback(callback, dependencies.presentation, locale)
                return
            except AdminOverviewError:
                await callback.answer(
                    dependencies.presentation.text("admin.refresh_failed", locale), show_alert=True
                )
                return
            await _edit_or_send(
                callback,
                dependencies.presentation.overview_text(result, locale),
                dependencies.presentation.keyboard(locale),
            )
            await callback.answer()
            return

        if action is AdminCallbackAction.CLOSE:
            await _close_panel(callback)
            await callback.answer()
            return
        await callback.answer(
            dependencies.presentation.text("admin.invalid_action", locale), show_alert=True
        )

    return router


async def _deny_callback(
    callback: CallbackQuery, presentation: AdminPresentation, locale: str
) -> None:
    try:
        if isinstance(callback.message, Message):
            await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramAPIError:
        pass
    await callback.answer(presentation.text("admin.access_denied", locale), show_alert=True)


async def _close_panel(callback: CallbackQuery) -> None:
    if not isinstance(callback.message, Message):
        return
    try:
        await callback.message.delete()
    except TelegramAPIError:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            pass


async def _edit_or_send(
    callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup
) -> None:
    if not isinstance(callback.message, Message):
        return
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramAPIError:
        await callback.message.answer(text, reply_markup=reply_markup)


async def _observe_message(message: Message, service: TelegramUserService) -> User | None:
    if message.from_user is None:
        return None
    return await service.observe(_profile(message.from_user))


async def _observe_callback(callback: CallbackQuery, service: TelegramUserService) -> User:
    return await service.observe(_profile(callback.from_user))


def _profile(user: AiogramUser) -> TelegramUserProfile:
    return TelegramUserProfile(user.id, user.username, user.first_name, user.language_code)
