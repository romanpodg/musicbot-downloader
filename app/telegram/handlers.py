"""Thin aiogram handlers for the Stage 9 downloader bot."""

from __future__ import annotations

from dataclasses import dataclass

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.types import User as AiogramUser

from app.core.enums import TelegramDeliveryStatus
from app.core.exceptions import (
    InvalidTrackUrl,
    MetadataUnavailable,
    ProviderAuthenticationError,
    ProviderUnavailable,
    UnsupportedMediaType,
    UnsupportedProvider,
)
from app.services.telegram_requests import (
    TelegramTrackRequestService,
    TrackRequestActionOutcome,
    TrackRequestActionResult,
)
from app.services.telegram_users import TelegramUserProfile, TelegramUserService
from app.storage.models import User
from app.telegram.presentation import (
    TelegramPresentation,
    parse_first_quality,
    parse_locale,
    parse_other_quality,
    parse_setting_quality,
    parse_track_back,
    parse_track_download,
    parse_track_quality,
)


@dataclass(frozen=True, slots=True)
class TelegramHandlerDependencies:
    users: TelegramUserService
    requests: TelegramTrackRequestService
    presentation: TelegramPresentation


def create_stage9_router(dependencies: TelegramHandlerDependencies) -> Router:
    router = Router(name="stage9-user-bot")

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        user = await _observe_message(message, dependencies.users)
        if user is None or not await _private(message, user, dependencies):
            return
        locale = dependencies.users.locale_for(user)
        await message.answer(dependencies.presentation.text("bot.welcome", locale))

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        user = await _observe_message(message, dependencies.users)
        if user is None or not await _private(message, user, dependencies):
            return
        locale = dependencies.users.locale_for(user)
        await message.answer(dependencies.presentation.text("bot.help", locale))

    @router.message(Command("quality"))
    async def quality_command(message: Message) -> None:
        user = await _observe_message(message, dependencies.users)
        if user is None or not await _private(message, user, dependencies):
            return
        locale = dependencies.users.locale_for(user)
        current = dependencies.presentation.quality_name(user.preferred_quality_profile, locale)
        await message.answer(
            dependencies.presentation.text("bot.choose_quality", locale, current=current),
            reply_markup=dependencies.presentation.quality_keyboard(locale),
        )

    @router.message(Command("language"))
    async def language_command(message: Message) -> None:
        user = await _observe_message(message, dependencies.users)
        if user is None or not await _private(message, user, dependencies):
            return
        locale = dependencies.users.locale_for(user)
        await message.answer(
            dependencies.presentation.text("bot.choose_language", locale),
            reply_markup=dependencies.presentation.language_keyboard(),
        )

    @router.callback_query(F.data.startswith("q1:"))
    async def first_quality(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        parsed = parse_first_quality(callback.data)
        if parsed is None or user is None:
            locale = (
                dependencies.users.locale_for(user)
                if user is not None
                else dependencies.presentation.i18n.default_locale
            )
            await callback.answer(
                dependencies.presentation.text("bot.invalid_selection", locale), show_alert=True
            )
            return
        locale = dependencies.users.locale_for(user)
        result = await dependencies.requests.choose_first_quality(
            request_id=parsed.request_id,
            telegram_user_id=callback.from_user.id,
            quality_profile=parsed.quality_profile,
        )
        if not result.accepted:
            await callback.answer(
                dependencies.presentation.text("bot.quality_stale", locale), show_alert=True
            )
            return
        text = dependencies.presentation.text(
            "bot.quality_saved_continuing",
            locale,
            quality=dependencies.presentation.quality_name(parsed.quality_profile, locale),
        )
        if isinstance(callback.message, Message):
            await callback.message.edit_text(text)
        await callback.answer()

    @router.callback_query(F.data.startswith("td1:"))
    async def track_download(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        request_id = parse_track_download(callback.data)
        if request_id is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        result = await dependencies.requests.start_default_quality(
            request_id=request_id, telegram_user_id=callback.from_user.id
        )
        if not result.accepted:
            await _answer_action_failure(callback, dependencies.presentation, locale, result)
            return
        await _remove_keyboard(callback)
        await callback.answer(
            dependencies.presentation.text("bot.preparing_download_short", locale)
        )

    @router.callback_query(F.data.startswith("to1:"))
    async def other_quality(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        request_id = parse_other_quality(callback.data)
        if request_id is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        result = await dependencies.requests.open_track_quality(
            request_id=request_id, telegram_user_id=callback.from_user.id
        )
        if not result.accepted:
            await _answer_action_failure(callback, dependencies.presentation, locale, result)
            return
        card = await dependencies.requests.track_card(
            request_id=request_id, telegram_user_id=callback.from_user.id
        )
        if card is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        await _edit_or_send(
            callback,
            dependencies.presentation.track_card_text(card, locale, mode="track_quality"),
            dependencies.presentation.track_quality_keyboard(locale, request_id=request_id),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("tq1:"))
    async def track_quality(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        parsed = parse_track_quality(callback.data)
        if parsed is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        result = await dependencies.requests.choose_track_quality(
            request_id=parsed.request_id,
            telegram_user_id=callback.from_user.id,
            quality_profile=parsed.quality_profile,
        )
        if not result.accepted:
            await _answer_action_failure(callback, dependencies.presentation, locale, result)
            return
        await _remove_keyboard(callback)
        await callback.answer(
            dependencies.presentation.text("bot.preparing_download_short", locale)
        )

    @router.callback_query(F.data.startswith("tb1:"))
    async def track_quality_back(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        request_id = parse_track_back(callback.data)
        if request_id is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        result = await dependencies.requests.back_to_track_card(
            request_id=request_id, telegram_user_id=callback.from_user.id
        )
        if not result.accepted:
            await _answer_action_failure(callback, dependencies.presentation, locale, result)
            return
        card = await dependencies.requests.track_card(
            request_id=request_id, telegram_user_id=callback.from_user.id
        )
        if card is None or card.quality_profile is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        await _edit_or_send(
            callback,
            dependencies.presentation.track_card_text(card, locale),
            dependencies.presentation.track_card_keyboard(
                locale, request_id=request_id, quality=card.quality_profile
            ),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("sq1:"))
    async def setting_quality(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        quality = parse_setting_quality(callback.data)
        if quality is None or user is None:
            locale = (
                dependencies.users.locale_for(user)
                if user is not None
                else dependencies.presentation.i18n.default_locale
            )
            await callback.answer(
                dependencies.presentation.text("bot.invalid_selection", locale), show_alert=True
            )
            return
        updated = await dependencies.users.set_quality(callback.from_user.id, quality)
        locale = dependencies.users.locale_for(updated)
        text = dependencies.presentation.text(
            "bot.quality_saved",
            locale,
            quality=dependencies.presentation.quality_name(quality, locale),
        )
        if isinstance(callback.message, Message):
            await callback.message.edit_text(text)
        await callback.answer()

    @router.callback_query(F.data.startswith("l1:"))
    async def setting_language(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = parse_locale(callback.data)
        if locale is None or user is None:
            selected = (
                dependencies.users.locale_for(user)
                if user is not None
                else dependencies.presentation.i18n.default_locale
            )
            await callback.answer(
                dependencies.presentation.text("bot.invalid_selection", selected), show_alert=True
            )
            return
        updated = await dependencies.users.set_locale(callback.from_user.id, locale)
        selected = dependencies.users.locale_for(updated)
        text = dependencies.presentation.text("bot.language_saved", selected)
        if isinstance(callback.message, Message):
            await callback.message.edit_text(text)
        await callback.answer()

    @router.message(F.text & ~F.text.startswith("/"))
    async def track_text(message: Message) -> None:
        user = await _observe_message(message, dependencies.users)
        if user is None or not await _private(message, user, dependencies):
            return
        locale = dependencies.users.locale_for(user)
        text = (message.text or "").strip()
        if not text.lower().startswith(("https://", "http://")):
            await message.answer(dependencies.presentation.text("bot.send_track_url_hint", locale))
            return
        try:
            request = await dependencies.requests.request_track(
                user=user,
                telegram_chat_id=message.chat.id,
                source_message_id=message.message_id,
                url=text,
            )
        except UnsupportedMediaType:
            await message.answer(
                dependencies.presentation.text("bot.unsupported_media_type", locale)
            )
            return
        except (InvalidTrackUrl, UnsupportedProvider):
            await message.answer(dependencies.presentation.text("bot.unsupported_url", locale))
            return
        except (MetadataUnavailable, ProviderUnavailable, ProviderAuthenticationError):
            await message.answer(
                dependencies.presentation.text("bot.track_resolution_failed", locale)
            )
            return
        if request.status is TelegramDeliveryStatus.AWAITING_QUALITY:
            card = await dependencies.requests.track_card(
                request_id=request.id, telegram_user_id=user.telegram_id
            )
            if card is None or card.card_message_id is not None:
                return
            sent = await message.answer(
                dependencies.presentation.track_card_text(card, locale, mode="first_quality"),
                reply_markup=dependencies.presentation.quality_keyboard(
                    locale, request_id=request.id
                ),
            )
            await _record_card_message(sent, request.id, user.telegram_id, dependencies.requests)
        elif request.status is TelegramDeliveryStatus.AWAITING_ACTION:
            card = await dependencies.requests.track_card(
                request_id=request.id, telegram_user_id=user.telegram_id
            )
            if card is None or card.card_message_id is not None or card.quality_profile is None:
                return
            sent = await message.answer(
                dependencies.presentation.track_card_text(card, locale),
                reply_markup=dependencies.presentation.track_card_keyboard(
                    locale, request_id=request.id, quality=card.quality_profile
                ),
            )
            await _record_card_message(sent, request.id, user.telegram_id, dependencies.requests)
        elif request.status is TelegramDeliveryStatus.AWAITING_TRACK_QUALITY:
            card = await dependencies.requests.track_card(
                request_id=request.id, telegram_user_id=user.telegram_id
            )
            if card is None or card.card_message_id is not None:
                return
            sent = await message.answer(
                dependencies.presentation.track_card_text(card, locale, mode="track_quality"),
                reply_markup=dependencies.presentation.track_quality_keyboard(
                    locale, request_id=request.id
                ),
            )
            await _record_card_message(sent, request.id, user.telegram_id, dependencies.requests)
        elif request.status is TelegramDeliveryStatus.DELIVERED:
            await message.answer(
                dependencies.presentation.text("bot.request_already_delivered", locale)
            )
        else:
            await message.answer(dependencies.presentation.text("bot.preparing_download", locale))

    return router


async def _record_card_message(
    sent: Message,
    request_id: int,
    telegram_user_id: int,
    service: TelegramTrackRequestService,
) -> None:
    if isinstance(sent, Message):
        await service.record_card_message(
            request_id=request_id,
            telegram_user_id=telegram_user_id,
            message_id=sent.message_id,
        )


async def _invalid_callback(
    callback: CallbackQuery, presentation: TelegramPresentation, locale: str
) -> None:
    await callback.answer(presentation.text("bot.invalid_selection", locale), show_alert=True)


async def _answer_action_failure(
    callback: CallbackQuery,
    presentation: TelegramPresentation,
    locale: str,
    result: TrackRequestActionResult,
) -> None:
    if result.outcome in {
        TrackRequestActionOutcome.FORBIDDEN,
        TrackRequestActionOutcome.NOT_FOUND,
    }:
        key = "bot.invalid_selection"
    elif result.request is not None and result.request.status is TelegramDeliveryStatus.DELIVERED:
        key = "bot.request_already_completed"
    else:
        key = "bot.request_already_started"
    await callback.answer(presentation.text(key, locale), show_alert=True)


async def _remove_keyboard(callback: CallbackQuery) -> None:
    if not isinstance(callback.message, Message):
        return
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


async def _private(
    message: Message,
    user: User,
    dependencies: TelegramHandlerDependencies,
) -> bool:
    if message.chat.type == "private":
        return True
    locale = dependencies.users.locale_for(user)
    await message.answer(dependencies.presentation.text("bot.private_only", locale))
    return False
