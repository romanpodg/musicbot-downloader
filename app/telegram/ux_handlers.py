"""Thin Stage 14 Telegram adapter for welcome, help, and menu navigation."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.types import User as AiogramUser

from app.application.download import DownloadConfirmation, DownloadService
from app.application.ux.flows.navigation import UxFlowService, UxMenu, UxScreen
from app.application.ux.services.errors import UxErrorService
from app.application.ux.services.state import UxState
from app.core.download import DownloadDeliveryTarget, DownloadSubmissionState
from app.services.telegram_requests import TelegramTrackRequestService
from app.services.telegram_users import TelegramUserProfile, TelegramUserService
from app.storage.models import User
from app.telegram.callbacks import parse_ux_callback
from app.telegram.download_callbacks import DownloadCallbackAction, parse_download_callback
from app.telegram.keyboards import UxKeyboardFactory
from app.telegram.messages import UxMessage, UxMessageService
from app.telegram.presentation import TelegramPresentation

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UxHandlerDependencies:
    users: TelegramUserService
    flows: UxFlowService
    messages: UxMessageService
    keyboards: UxKeyboardFactory
    errors: UxErrorService
    downloads: DownloadService | None = None
    track_requests: TelegramTrackRequestService | None = None
    delivery_presentation: TelegramPresentation | None = None


class SearchInputFilter(BaseFilter):
    """Match only an active Stage 15 search input, preserving later text routers."""

    def __init__(self, flows: UxFlowService) -> None:
        self._flows = flows

    async def __call__(self, message: Message) -> bool:
        return (
            message.from_user is not None
            and message.text is not None
            and not message.text.startswith("/")
            and self._flows.awaiting_search_input(message.from_user.id)
        )


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

    @router.message(Command("search"))
    async def search_command(message: Message) -> None:
        await _handle_message(message, dependencies, dependencies.flows.begin_search)

    @router.message(SearchInputFilter(dependencies.flows))
    async def search_input(message: Message) -> None:
        if message.from_user is None or message.text is None:
            return
        try:
            user = await dependencies.users.observe(_profile(message.from_user))
            if not await _private_message(message, user, dependencies):
                return
            screen = await dependencies.flows.search(_profile(message.from_user), message.text)
            locale = dependencies.users.locale_for(user)
            await message.answer(
                _screen_text(screen, dependencies, locale),
                reply_markup=_screen_keyboard(screen, dependencies, locale),
            )
        except Exception as exc:
            logger.error("Telegram UX search failed")
            locale = dependencies.messages.default_locale
            await message.answer(
                dependencies.messages.get(dependencies.errors.message_name(exc).value, locale)
            )

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

    @router.callback_query(F.data.startswith("dl18:"))
    async def download_callback(callback: CallbackQuery) -> None:
        parsed = parse_download_callback(callback.data)
        if parsed is None or dependencies.downloads is None:
            await _invalid_callback(callback, dependencies)
            return
        try:
            user = await dependencies.users.observe(_profile(callback.from_user))
            if not await _private_callback(callback, user, dependencies):
                return
            locale = dependencies.users.locale_for(user)
            if parsed.action is DownloadCallbackAction.CANCEL:
                if not dependencies.downloads.cancel(
                    user_id=callback.from_user.id, token=parsed.token
                ):
                    await _invalid_callback(callback, dependencies)
                    return
                dependencies.flows.transition(callback.from_user.id, UxState.IDLE)
                await _edit_download_text(
                    callback,
                    dependencies.messages.get(UxMessage.DOWNLOAD_CANCELLED, locale),
                )
                await callback.answer()
                return
            if parsed.action is DownloadCallbackAction.SELECT:
                assert parsed.alternative_index is not None
                confirmation = dependencies.downloads.select_alternative(
                    user_id=callback.from_user.id,
                    token=parsed.token,
                    alternative_index=parsed.alternative_index,
                )
                if confirmation is None:
                    await _invalid_callback(callback, dependencies)
                    return
                await _edit_download_confirmation(callback, dependencies, locale, confirmation)
                await callback.answer()
                return
            if not isinstance(callback.message, Message):
                await _invalid_callback(callback, dependencies)
                return
            submission = await dependencies.downloads.confirm(
                user_id=callback.from_user.id,
                token=parsed.token,
                target=DownloadDeliveryTarget(
                    user_id=callback.from_user.id,
                    destination_id=callback.message.chat.id,
                    source_message_id=callback.message.message_id,
                ),
            )
            if submission is None:
                await _invalid_callback(callback, dependencies)
                return
            if submission.state is DownloadSubmissionState.AWAITING_QUALITY:
                await _render_initial_quality(
                    callback, dependencies, locale, submission.delivery_request_id
                )
            else:
                dependencies.flows.transition(callback.from_user.id, UxState.DOWNLOAD_QUEUED)
                await _edit_download_text(
                    callback,
                    dependencies.messages.get(UxMessage.DOWNLOAD_QUEUED, locale),
                )
            await callback.answer()
        except Exception as exc:
            logger.error("Telegram download confirmation failed")
            await _operation_failed_callback(callback, dependencies, exc, download=True)

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
            _screen_text(screen, dependencies, locale),
            reply_markup=_screen_keyboard(screen, dependencies, locale),
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
        _screen_text(screen, dependencies, locale),
        reply_markup=_screen_keyboard(screen, dependencies, locale),
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
    callback: CallbackQuery,
    dependencies: UxHandlerDependencies,
    error: Exception,
    *,
    download: bool = False,
) -> None:
    locale = dependencies.messages.default_locale
    try:
        user = await dependencies.users.observe(_profile(callback.from_user))
        locale = dependencies.users.locale_for(user)
    except Exception:
        pass
    await callback.answer(
        dependencies.messages.get(
            (
                dependencies.errors.download_message_name(error)
                if download
                else dependencies.errors.message_name(error)
            ).value,
            locale,
        ),
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


def _screen_text(screen: UxScreen, dependencies: UxHandlerDependencies, locale: str) -> str:
    if screen.download_confirmation is not None:
        return _confirmation_text(screen.download_confirmation, dependencies, locale)
    return dependencies.messages.get(screen.message_key.removeprefix("ux."), locale)


def _screen_keyboard(
    screen: UxScreen, dependencies: UxHandlerDependencies, locale: str
) -> InlineKeyboardMarkup | None:
    if screen.download_confirmation is not None:
        return dependencies.keyboards.download_confirmation(locale, screen.download_confirmation)
    return dependencies.keyboards.for_menu(locale, screen.menu)


def _confirmation_text(
    confirmation: DownloadConfirmation, dependencies: UxHandlerDependencies, locale: str
) -> str:
    track = confirmation.selected_track
    artist = ", ".join(item.name for item in track.artists)
    text = dependencies.messages.get(
        UxMessage.DOWNLOAD_CONFIRMATION, locale, artist=artist, title=track.title
    )
    if confirmation.alternatives:
        alternatives = "\n".join(
            f"• {', '.join(item.name for item in item_track.artists)} — {item_track.title}"
            for item_track in confirmation.alternatives
        )
        text += dependencies.messages.get(
            UxMessage.DOWNLOAD_ALTERNATIVES, locale, alternatives=alternatives
        )
    return text


async def _edit_download_confirmation(
    callback: CallbackQuery,
    dependencies: UxHandlerDependencies,
    locale: str,
    confirmation: DownloadConfirmation,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _confirmation_text(confirmation, dependencies, locale),
            reply_markup=dependencies.keyboards.download_confirmation(locale, confirmation),
        )


async def _edit_download_text(callback: CallbackQuery, text: str) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=None)


async def _render_initial_quality(
    callback: CallbackQuery,
    dependencies: UxHandlerDependencies,
    locale: str,
    delivery_request_id: int,
) -> None:
    if (
        not isinstance(callback.message, Message)
        or dependencies.track_requests is None
        or dependencies.delivery_presentation is None
    ):
        raise RuntimeError("download quality presentation is not composed")
    card = await dependencies.track_requests.track_card(
        request_id=delivery_request_id, telegram_user_id=callback.from_user.id
    )
    if card is None:
        raise ValueError("download quality request is no longer available")
    await callback.message.edit_text(
        dependencies.delivery_presentation.track_card_text(card, locale, mode="first_quality"),
        reply_markup=dependencies.delivery_presentation.quality_keyboard(
            locale, request_id=delivery_request_id
        ),
    )


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
