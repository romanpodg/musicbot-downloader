"""Thin Stage 14 Telegram adapter for welcome, help, and menu navigation."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command, CommandStart
from aiogram.types import CallbackQuery, ForceReply, InlineKeyboardMarkup, Message
from aiogram.types import User as AiogramUser

from app.application.download import DownloadConfirmation, DownloadService
from app.application.ux.flows.navigation import UxFlowService, UxMenu, UxScreen
from app.application.ux.services.errors import UxErrorService
from app.application.ux.services.state import UxState
from app.core.delivery_targets import DeliveryTarget, PrivateUserTarget
from app.core.download import DownloadDeliveryTarget, DownloadSubmissionState
from app.core.telegram_context import TelegramChatType, TelegramContext
from app.services.telegram_context import ChatContextAccessService
from app.services.telegram_requests import TelegramTrackRequestService
from app.services.telegram_users import TelegramUserProfile, TelegramUserService
from app.storage.models import User
from app.telegram.callbacks import parse_ux_callback
from app.telegram.context import telegram_context_from_values
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
    contexts: ChatContextAccessService | None = None


class SearchInputFilter(BaseFilter):
    """Match only an active Stage 15 search input, preserving later text routers."""

    def __init__(self, flows: UxFlowService) -> None:
        self._flows = flows

    async def __call__(self, message: Message, bot: Bot) -> bool:
        context = _message_context(message)
        return (
            context is not None
            and message.text is not None
            and not message.text.startswith("/")
            and self._flows.awaiting_search_input(context)
            and (
                context.chat_type is TelegramChatType.PRIVATE
                or _is_bot_directed_reply(message, bot)
            )
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
        query = _command_query(message.text)
        if query is None:
            await _handle_message(
                message,
                dependencies,
                dependencies.flows.begin_search,
                group_reply_prompt=True,
            )
            return
        await _handle_search_query(message, dependencies, query)

    @router.message(SearchInputFilter(dependencies.flows))
    async def search_input(message: Message) -> None:
        if message.from_user is None or message.text is None:
            return
        try:
            user = await dependencies.users.observe(_profile(message.from_user))
            context, _ = await _message_access(message, user, dependencies)
            if context is None:
                return
            screen = await dependencies.flows.search(
                _profile(message.from_user), message.text, context=context
            )
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
            context, _ = await _callback_access(callback, user, dependencies)
            if context is None:
                return
            screen = await dependencies.flows.open_menu(
                _profile(callback.from_user), menu, context=context
            )
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
            context, target = await _callback_access(callback, user, dependencies)
            if context is None or target is None:
                return
            locale = dependencies.users.locale_for(user)
            if parsed.action is DownloadCallbackAction.CANCEL:
                if not dependencies.downloads.cancel(context=context, token=parsed.token):
                    await _invalid_callback(callback, dependencies)
                    return
                dependencies.flows.transition(context, UxState.IDLE)
                await _edit_download_text(
                    callback,
                    dependencies.messages.get(UxMessage.DOWNLOAD_CANCELLED, locale),
                )
                await callback.answer()
                return
            if parsed.action is DownloadCallbackAction.SELECT:
                assert parsed.alternative_index is not None
                confirmation = dependencies.downloads.select_alternative(
                    context=context,
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
                context=context,
                token=parsed.token,
                target=DownloadDeliveryTarget(
                    user_id=callback.from_user.id,
                    context=context,
                    delivery_target=target,
                    source_message_id=callback.message.message_id,
                ),
            )
            if submission is None:
                await _invalid_callback(callback, dependencies)
                return
            if submission.state is DownloadSubmissionState.AWAITING_QUALITY:
                await _render_initial_quality(
                    callback,
                    dependencies,
                    locale,
                    submission.delivery_request_id,
                    context,
                )
            else:
                dependencies.flows.transition(context, UxState.DOWNLOAD_QUEUED)
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
    operation: Callable[..., Awaitable[UxScreen]],
    *,
    group_reply_prompt: bool = False,
) -> None:
    if message.from_user is None:
        return
    try:
        user = await dependencies.users.observe(_profile(message.from_user))
        context, _ = await _message_access(message, user, dependencies)
        if context is None:
            return
        screen = await _invoke_operation(operation, _profile(message.from_user), context)
        locale = dependencies.users.locale_for(user)
        await message.answer(
            _screen_text(screen, dependencies, locale),
            reply_markup=(
                ForceReply(selective=True)
                if group_reply_prompt
                and context.chat_type in {TelegramChatType.GROUP, TelegramChatType.SUPERGROUP}
                else _screen_keyboard(screen, dependencies, locale)
            ),
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
    if confirmation.presentation_alternatives:
        alternatives = "\n".join(
            f"• {', '.join(item.name for item in item_track.artists)} — {item_track.title}"
            for item_track in confirmation.presentation_alternatives
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
    context: TelegramContext,
) -> None:
    if (
        not isinstance(callback.message, Message)
        or dependencies.track_requests is None
        or dependencies.delivery_presentation is None
    ):
        raise RuntimeError("download quality presentation is not composed")
    card = await dependencies.track_requests.track_card(
        request_id=delivery_request_id,
        telegram_user_id=callback.from_user.id,
        telegram_chat_id=context.chat_id,
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


async def _invoke_operation(
    operation: Callable[..., Awaitable[UxScreen]],
    profile: TelegramUserProfile,
    context: TelegramContext,
) -> UxScreen:
    """Pass context to Stage 20-aware flows while keeping old injected test doubles usable."""

    import inspect

    if "context" in inspect.signature(operation).parameters:
        return await operation(profile, context=context)
    return await operation(profile)


async def _handle_search_query(
    message: Message, dependencies: UxHandlerDependencies, query: str
) -> None:
    if message.from_user is None:
        return
    try:
        user = await dependencies.users.observe(_profile(message.from_user))
        context, _ = await _message_access(message, user, dependencies)
        if context is None:
            return
        screen = await dependencies.flows.search(
            _profile(message.from_user), query, context=context
        )
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


def _command_query(text: str | None) -> str | None:
    if not text:
        return None
    _, _, remainder = text.partition(" ")
    remainder = remainder.strip()
    return remainder or None


def _is_bot_directed_reply(message: Message, bot: Bot) -> bool:
    reply = message.reply_to_message
    return bool(reply is not None and reply.from_user is not None and reply.from_user.id == bot.id)


async def _message_access(
    message: Message, user: User, dependencies: UxHandlerDependencies
) -> tuple[TelegramContext | None, DeliveryTarget | None]:
    context = _message_context(message)
    if context is None:
        return None, None
    return await _context_access(context, user, dependencies, message.answer)


async def _callback_access(
    callback: CallbackQuery, user: User, dependencies: UxHandlerDependencies
) -> tuple[TelegramContext | None, DeliveryTarget | None]:
    if not isinstance(callback.message, Message):
        await _invalid_callback(callback, dependencies)
        return None, None
    context = telegram_context_from_values(
        callback.from_user.id, callback.message.chat.id, callback.message.chat.type
    )
    if context is None:
        await _invalid_callback(callback, dependencies)
        return None, None
    return await _context_access(
        context,
        user,
        dependencies,
        lambda text: callback.answer(text, show_alert=True),
    )


async def _context_access(
    context: TelegramContext,
    user: User,
    dependencies: UxHandlerDependencies,
    deny: Callable[[str], Awaitable[object]],
) -> tuple[TelegramContext | None, DeliveryTarget | None]:
    if dependencies.contexts is None:
        if context.chat_type is TelegramChatType.PRIVATE:
            return context, PrivateUserTarget(context.user_id)
        locale = dependencies.users.locale_for(user)
        await deny(dependencies.messages.get(UxMessage.PRIVATE_ONLY, locale))
        return None, None
    result = await dependencies.contexts.resolve(context, user)
    if result.allowed:
        return context, result.target
    locale = dependencies.users.locale_for(user)
    await deny(dependencies.messages.get(UxMessage.CHAT_ACCESS_DENIED, locale))
    return None, None


def _message_context(message: Message) -> TelegramContext | None:
    if message.from_user is None:
        return None
    return telegram_context_from_values(message.from_user.id, message.chat.id, message.chat.type)
