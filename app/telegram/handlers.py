"""Thin aiogram handlers for the Stage 9 downloader bot."""

from __future__ import annotations

from dataclasses import dataclass

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.types import User as AiogramUser

from app.core.delivery_targets import PrivateUserTarget
from app.core.download import DownloadDeliveryTarget
from app.core.enums import AlbumRequestStatus, DeepLinkTargetType, TelegramDeliveryStatus
from app.core.exceptions import (
    AlbumResolutionFailed,
    AlbumTooLarge,
    InvalidTrackUrl,
    MetadataUnavailable,
    ProviderAuthenticationError,
    ProviderUnavailable,
    UnsupportedMediaType,
    UnsupportedProvider,
)
from app.core.telegram_context import TelegramChatType
from app.services.batch_download import (
    ActiveBatchLimitExceeded,
    BatchDownloadService,
    BatchLimitExceeded,
)
from app.services.deep_links import DeepLinkRegistryService
from app.services.download_history import DownloadHistoryService
from app.services.download_preferences import UserDownloadPreferencesService
from app.services.telegram_albums import (
    AlbumActionOutcome,
    AlbumActionResult,
    TelegramAlbumRequestService,
)
from app.services.telegram_context import ChatContextAccessService
from app.services.telegram_media_requests import TelegramMediaRequestService
from app.services.telegram_requests import (
    TelegramTrackRequestService,
    TrackRequestActionOutcome,
    TrackRequestActionResult,
)
from app.services.telegram_users import TelegramUserProfile, TelegramUserService
from app.storage.models import User
from app.telegram.context import telegram_context_from_values
from app.telegram.presentation import (
    TelegramPresentation,
    parse_album_clear_all,
    parse_album_download_all,
    parse_album_download_selected,
    parse_album_first_quality,
    parse_album_other_quality,
    parse_album_page,
    parse_album_quality,
    parse_album_quality_back,
    parse_album_select_all,
    parse_album_select_tracks,
    parse_album_selection_back,
    parse_album_toggle,
    parse_batch_cancel,
    parse_batch_download,
    parse_batch_retry,
    parse_first_quality,
    parse_history_callback,
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
    media: TelegramMediaRequestService | None = None
    albums: TelegramAlbumRequestService | None = None
    deep_links: DeepLinkRegistryService | None = None
    contexts: ChatContextAccessService | None = None
    batch_download: BatchDownloadService | None = None
    download_preferences: UserDownloadPreferencesService | None = None
    history: DownloadHistoryService | None = None
    telegram_bot_id: int | None = None


def create_stage9_router(dependencies: TelegramHandlerDependencies) -> Router:
    router = Router(name="stage9-user-bot")

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        user = await _observe_message(message, dependencies.users)
        if user is None or not await _private(message, user, dependencies):
            return
        locale = dependencies.users.locale_for(user)
        payload = _start_payload(message.text)
        if (
            payload is not None
            and dependencies.deep_links is not None
            and dependencies.deep_links.is_namespaced_payload(payload)
        ):
            entry = await dependencies.deep_links.resolve_start_payload(payload)
            if entry is None:
                await message.answer(
                    dependencies.presentation.text("bot.deep_link_unavailable", locale)
                )
                return
            try:
                if entry.target_type is DeepLinkTargetType.TRACK and entry.track_id is not None:
                    request = await dependencies.requests.request_track_id(
                        user=user,
                        telegram_chat_id=message.chat.id,
                        source_message_id=message.message_id,
                        track_id=entry.track_id,
                    )
                    await _send_track_request_card(message, user, request.id, dependencies)
                    return
                if (
                    entry.target_type is DeepLinkTargetType.ALBUM
                    and entry.album_provider is not None
                    and entry.album_provider_id is not None
                    and dependencies.albums is not None
                ):
                    album = await dependencies.albums.request_album_target(
                        user=user,
                        telegram_chat_id=message.chat.id,
                        source_message_id=message.message_id,
                        provider=entry.album_provider,
                        provider_album_id=entry.album_provider_id,
                    )
                    await _send_album_request_card(message, user, album.id, dependencies)
                    return
            except (
                AlbumTooLarge,
                MetadataUnavailable,
                ProviderAuthenticationError,
                ProviderUnavailable,
            ):
                pass
            await message.answer(
                dependencies.presentation.text("bot.deep_link_unavailable", locale)
            )
            return
        await message.answer(dependencies.presentation.text("bot.welcome", locale))

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        user = await _observe_message(message, dependencies.users)
        if user is None or not await _private(message, user, dependencies):
            return
        locale = dependencies.users.locale_for(user)
        await message.answer(dependencies.presentation.text("bot.help", locale))

    @router.message(Command("history"))
    async def history_command(message: Message) -> None:
        if dependencies.history is None or message.from_user is None:
            return
        user = await _observe_message(message, dependencies.users)
        if user is None or not await _private(message, user, dependencies):
            return
        page = await dependencies.history.page(user.telegram_id)
        locale = dependencies.users.locale_for(user)
        await message.answer(
            dependencies.presentation.history_text(page, locale),
            reply_markup=dependencies.presentation.history_keyboard(page, locale),
        )

    @router.callback_query(F.data.startswith("h24:"))
    async def history_callback(callback: CallbackQuery) -> None:
        if dependencies.history is None:
            await _invalid_callback(
                callback, dependencies.presentation, dependencies.presentation.i18n.default_locale
            )
            return
        parsed = parse_history_callback(callback.data)
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        if parsed is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        try:
            if parsed.action == "list":
                page = await dependencies.history.page(user.telegram_id, cursor=parsed.cursor)
                await _edit_or_send(
                    callback,
                    dependencies.presentation.history_text(page, locale),
                    dependencies.presentation.history_keyboard(page, locale)
                    or InlineKeyboardMarkup(inline_keyboard=[]),
                )
            elif parsed.action == "track" and parsed.identifier is not None:
                entry = await dependencies.history.track(user.telegram_id, parsed.identifier)
                if entry is None:
                    await _invalid_callback(callback, dependencies.presentation, locale)
                    return
                await _edit_or_send(
                    callback,
                    dependencies.presentation.history_track_text(entry, locale),
                    dependencies.presentation.history_track_keyboard(entry, locale),
                )
            elif parsed.action == "batch" and parsed.identifier is not None:
                batch_entry = await dependencies.history.batch(user.telegram_id, parsed.identifier)
                if batch_entry is None:
                    await _invalid_callback(callback, dependencies.presentation, locale)
                    return
                await _edit_or_send(
                    callback,
                    dependencies.presentation.history_batch_text(batch_entry, locale),
                    dependencies.presentation.history_batch_keyboard(batch_entry, locale),
                )
            elif (
                parsed.action == "brepeat"
                and parsed.identifier is not None
                and isinstance(callback.message, Message)
                and dependencies.batch_download is not None
            ):
                target = _batch_target(callback, user.telegram_id)
                batch = await dependencies.history.repeat_batch(
                    user.telegram_id, parsed.identifier, target=target
                )
                if batch is None:
                    await _invalid_callback(callback, dependencies.presentation, locale)
                    return
                await dependencies.batch_download.admit_pending(batch.id, target=target)
                await callback.message.edit_text("Download queued")
            elif (
                parsed.action == "repeat"
                and parsed.identifier is not None
                and isinstance(callback.message, Message)
            ):
                result = await dependencies.history.repeat(
                    user.telegram_id,
                    parsed.identifier,
                    target=_batch_target(callback, user.telegram_id),
                )
                if result is None:
                    await _invalid_callback(callback, dependencies.presentation, locale)
                    return
                await callback.message.edit_text("Download queued")
            else:
                await _invalid_callback(callback, dependencies.presentation, locale)
                return
            await callback.answer()
        except (ValueError, TypeError):
            await _invalid_callback(callback, dependencies.presentation, locale)

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
        if not isinstance(callback.message, Message):
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        context = telegram_context_from_values(
            callback.from_user.id, callback.message.chat.id, callback.message.chat.type
        )
        if context is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        if dependencies.contexts is None:
            if context.chat_type is not TelegramChatType.PRIVATE:
                await _invalid_callback(callback, dependencies.presentation, locale)
                return
        else:
            access = await dependencies.contexts.resolve(context, user)
            if not access.allowed:
                await _invalid_callback(callback, dependencies.presentation, locale)
                return
        result = await dependencies.requests.choose_first_quality(
            request_id=parsed.request_id,
            telegram_user_id=callback.from_user.id,
            telegram_chat_id=context.chat_id,
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
        # Acknowledge before the durable admission path so Telegram never keeps
        # a spinner while SQLite/queue work is taking place.
        await callback.answer()
        result = await dependencies.requests.start_default_quality(
            request_id=request_id, telegram_user_id=callback.from_user.id
        )
        if not result.accepted:
            await _answer_action_failure(
                callback, dependencies.presentation, locale, result, acknowledged=True
            )
            return
        card = await dependencies.requests.track_card(
            request_id=request_id, telegram_user_id=callback.from_user.id
        )
        if card is None:
            return
        await _edit_or_send(
            callback,
            f"{dependencies.presentation.track_card_text(card, locale)}\n\nPreparing…",
            InlineKeyboardMarkup(inline_keyboard=[]),
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
        await callback.answer()
        result = await dependencies.requests.choose_track_quality(
            request_id=parsed.request_id,
            telegram_user_id=callback.from_user.id,
            quality_profile=parsed.quality_profile,
        )
        if not result.accepted:
            await _answer_action_failure(
                callback, dependencies.presentation, locale, result, acknowledged=True
            )
            return
        card = await dependencies.requests.track_card(
            request_id=parsed.request_id, telegram_user_id=callback.from_user.id
        )
        if card is not None:
            await _edit_or_send(
                callback,
                f"{dependencies.presentation.track_card_text(card, locale)}\n\nPreparing…",
                InlineKeyboardMarkup(inline_keyboard=[]),
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

    @router.callback_query(F.data.startswith("af1:"))
    async def album_first_quality(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        parsed = parse_album_first_quality(callback.data)
        if parsed is None or dependencies.albums is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        result = await dependencies.albums.choose_first_quality(
            request_id=parsed.request_id,
            telegram_user_id=callback.from_user.id,
            quality_profile=parsed.quality_profile,
        )
        if not result.accepted:
            await _answer_album_failure(callback, dependencies.presentation, locale, result)
            return
        await _render_album_card(callback, dependencies, parsed.request_id, locale)
        await callback.answer(
            dependencies.presentation.text("bot.album_default_quality_saved", locale)
        )

    @router.callback_query(F.data.startswith("ad1:"))
    async def album_download_all(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        request_id = parse_album_download_all(callback.data)
        if request_id is None or dependencies.albums is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        # Stage 23 batch admission is the only path for album Download all.
        # The legacy album coordinator remains available for older callers but
        # is deliberately not woken by this Telegram action.
        if dependencies.batch_download is not None:
            collection = await dependencies.albums.collection(
                request_id=request_id, telegram_user_id=callback.from_user.id
            )
            if collection is None:
                await _invalid_callback(callback, dependencies.presentation, locale)
                return
            preferences = (
                await dependencies.download_preferences.get_for_telegram_user(callback.from_user.id)
                if dependencies.download_preferences is not None
                else None
            )
            if preferences is None:
                await _invalid_callback(callback, dependencies.presentation, locale)
                return
            try:
                batch = await dependencies.batch_download.create_from_collection(
                    user_id=user.id,
                    confirmation_id=f"album:{request_id}",
                    collection=collection,
                    preferences=preferences,
                )
            except BatchLimitExceeded:
                await callback.answer(
                    dependencies.presentation.text("bot.album_too_large", locale), show_alert=True
                )
                return
            except ActiveBatchLimitExceeded:
                await callback.answer(
                    dependencies.presentation.text("bot.album_request_stale", locale),
                    show_alert=True,
                )
                return
            target = _batch_target(callback, user.telegram_id)
            await dependencies.batch_download.admit_pending(batch.id, target=target)
            await _remove_keyboard(callback)
            await _render_batch(callback, dependencies, batch.id, locale)
            if isinstance(callback.message, Message) and dependencies.telegram_bot_id is not None:
                await dependencies.batch_download.record_parent_message(
                    batch_id=batch.id,
                    user_id=user.id,
                    bot_id=dependencies.telegram_bot_id,
                    chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                )
            await callback.answer(dependencies.presentation.text("bot.album_preparing", locale))
            return
        result = await dependencies.albums.download_all(
            request_id=request_id, telegram_user_id=callback.from_user.id
        )
        if not result.accepted:
            await _answer_album_failure(callback, dependencies.presentation, locale, result)
            return
        await _remove_keyboard(callback)
        await callback.answer(dependencies.presentation.text("bot.album_preparing", locale))

    @router.callback_query(F.data.startswith("bc1:"))
    async def batch_cancel(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        batch_id = parse_batch_cancel(callback.data)
        if batch_id is None or dependencies.batch_download is None or user is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        changed = await dependencies.batch_download.cancel(batch_id, user_id=user.id)
        if not changed:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        await dependencies.batch_download.reconcile(batch_id)
        await _render_batch(callback, dependencies, batch_id, locale)
        await callback.answer()

    @router.callback_query(F.data.startswith("bd1:"))
    async def batch_status(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        batch_id = parse_batch_download(callback.data)
        if batch_id is None or dependencies.batch_download is None or user is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        if not await _render_batch(callback, dependencies, batch_id, locale):
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        await callback.answer()

    @router.callback_query(F.data.startswith("br1:"))
    async def batch_retry(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        batch_id = parse_batch_retry(callback.data)
        if batch_id is None or dependencies.batch_download is None or user is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        retry = await dependencies.batch_download.retry_failed(batch_id, user_id=user.id)
        if retry is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        await dependencies.batch_download.admit_pending(
            retry.id, target=_batch_target(callback, user.telegram_id)
        )
        await _render_batch(callback, dependencies, retry.id, locale)
        await callback.answer()

    @router.callback_query(F.data.startswith("as1:"))
    async def album_select_tracks(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        request_id = parse_album_select_tracks(callback.data)
        if request_id is None or dependencies.albums is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        result = await dependencies.albums.open_selection(
            request_id=request_id, telegram_user_id=callback.from_user.id
        )
        if not result.accepted:
            await _answer_album_failure(callback, dependencies.presentation, locale, result)
            return
        await _render_album_page(callback, dependencies, request_id, 0, locale)
        await callback.answer()

    @router.callback_query(F.data.startswith("ao1:"))
    async def album_other_quality(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        request_id = parse_album_other_quality(callback.data)
        if request_id is None or dependencies.albums is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        result = await dependencies.albums.open_quality(
            request_id=request_id, telegram_user_id=callback.from_user.id
        )
        if not result.accepted:
            await _answer_album_failure(callback, dependencies.presentation, locale, result)
            return
        card = await dependencies.albums.card(
            request_id=request_id, telegram_user_id=callback.from_user.id
        )
        if card is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        await _edit_or_send(
            callback,
            dependencies.presentation.album_card_text(card, locale, mode="album_quality"),
            dependencies.presentation.album_quality_keyboard(locale, request_id=request_id),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("aq1:"))
    async def album_quality(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        parsed = parse_album_quality(callback.data)
        if parsed is None or dependencies.albums is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        result = await dependencies.albums.choose_quality(
            request_id=parsed.request_id,
            telegram_user_id=callback.from_user.id,
            quality_profile=parsed.quality_profile,
        )
        if not result.accepted:
            await _answer_album_failure(callback, dependencies.presentation, locale, result)
            return
        await _render_album_card(callback, dependencies, parsed.request_id, locale)
        await callback.answer()

    @router.callback_query(F.data.startswith("ak1:"))
    async def album_quality_back(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        request_id = parse_album_quality_back(callback.data)
        if request_id is None or dependencies.albums is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        result = await dependencies.albums.back_from_quality(
            request_id=request_id, telegram_user_id=callback.from_user.id
        )
        if not result.accepted:
            await _answer_album_failure(callback, dependencies.presentation, locale, result)
            return
        await _render_album_card(callback, dependencies, request_id, locale)
        await callback.answer()

    @router.callback_query(F.data.startswith("at1:"))
    async def album_toggle(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        parsed = parse_album_toggle(callback.data)
        if parsed is None or dependencies.albums is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        result = await dependencies.albums.toggle(
            request_id=parsed.request_id,
            item_id=parsed.item_id,
            telegram_user_id=callback.from_user.id,
        )
        if not result.accepted:
            await _answer_album_failure(callback, dependencies.presentation, locale, result)
            return
        await _render_album_page(callback, dependencies, parsed.request_id, parsed.page, locale)
        await callback.answer()

    @router.callback_query(F.data.startswith("ap1:"))
    async def album_page(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        parsed = parse_album_page(callback.data)
        if parsed is None or dependencies.albums is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        if not await _render_album_page(
            callback, dependencies, parsed.request_id, parsed.page, locale
        ):
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        await callback.answer()

    @router.callback_query(F.data.startswith("ax1:"))
    async def album_select_all(callback: CallbackQuery) -> None:
        await _album_bulk_selection(callback, dependencies, selected=True)

    @router.callback_query(F.data.startswith("ac1:"))
    async def album_clear_all(callback: CallbackQuery) -> None:
        await _album_bulk_selection(callback, dependencies, selected=False)

    @router.callback_query(F.data.startswith("aa1:"))
    async def album_download_selected(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        request_id = parse_album_download_selected(callback.data)
        if request_id is None or dependencies.albums is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        result = await dependencies.albums.download_selected(
            request_id=request_id, telegram_user_id=callback.from_user.id
        )
        if result.outcome is AlbumActionOutcome.EMPTY:
            await callback.answer(
                dependencies.presentation.text("bot.album_select_at_least_one", locale),
                show_alert=True,
            )
            return
        if not result.accepted:
            await _answer_album_failure(callback, dependencies.presentation, locale, result)
            return
        await _remove_keyboard(callback)
        await callback.answer(dependencies.presentation.text("bot.album_preparing", locale))

    @router.callback_query(F.data.startswith("ab1:"))
    async def album_selection_back(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        request_id = parse_album_selection_back(callback.data)
        if request_id is None or dependencies.albums is None:
            await _invalid_callback(callback, dependencies.presentation, locale)
            return
        result = await dependencies.albums.back_from_selection(
            request_id=request_id, telegram_user_id=callback.from_user.id
        )
        if not result.accepted:
            await _answer_album_failure(callback, dependencies.presentation, locale, result)
            return
        await _render_album_card(callback, dependencies, request_id, locale)
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
            if dependencies.media is None:
                request = await dependencies.requests.request_track(
                    user=user,
                    telegram_chat_id=message.chat.id,
                    source_message_id=message.message_id,
                    url=text,
                )
            else:
                admission = await dependencies.media.request(
                    user=user,
                    telegram_chat_id=message.chat.id,
                    source_message_id=message.message_id,
                    url=text,
                )
                if admission.album is not None:
                    await _send_album_request_card(message, user, admission.album.id, dependencies)
                    return
                if admission.batch is not None:
                    if dependencies.batch_download is None:
                        raise UnsupportedMediaType()
                    batch = admission.batch
                    await dependencies.batch_download.admit_pending(
                        batch.id, target=_message_batch_target(message, user.telegram_id)
                    )
                    progress = await dependencies.batch_download.progress(batch.id)
                    if progress is not None:
                        sent = await message.answer(
                            dependencies.presentation.batch_progress_text(
                                batch.title, progress, locale, terminal=False
                            ),
                            reply_markup=dependencies.presentation.batch_progress_keyboard(
                                locale, batch_id=batch.id
                            ),
                        )
                        if dependencies.telegram_bot_id is not None:
                            await dependencies.batch_download.record_parent_message(
                                batch_id=batch.id,
                                user_id=user.id,
                                bot_id=dependencies.telegram_bot_id,
                                chat_id=sent.chat.id,
                                message_id=sent.message_id,
                            )
                    return
                if admission.track is None:
                    raise UnsupportedMediaType()
                request = admission.track
        except UnsupportedMediaType:
            await message.answer(
                dependencies.presentation.text("bot.unsupported_media_type", locale)
            )
            return
        except AlbumTooLarge:
            await message.answer(dependencies.presentation.text("bot.album_too_large", locale))
            return
        except (InvalidTrackUrl, UnsupportedProvider):
            await message.answer(dependencies.presentation.text("bot.unsupported_url", locale))
            return
        except AlbumResolutionFailed:
            await message.answer(
                dependencies.presentation.text("bot.album_resolution_failed", locale)
            )
            return
        except (MetadataUnavailable, ProviderUnavailable, ProviderAuthenticationError):
            await message.answer(
                dependencies.presentation.text("bot.track_resolution_failed", locale)
            )
            return
        await _send_track_request_card(message, user, request.id, dependencies)

    return router


def _start_payload(text: str | None) -> str | None:
    if text is None:
        return None
    parts = text.strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) == 2 and parts[1].strip() else None


async def _send_track_request_card(
    message: Message,
    user: User,
    request_id: int,
    dependencies: TelegramHandlerDependencies,
) -> None:
    locale = dependencies.users.locale_for(user)
    card = await dependencies.requests.track_card(
        request_id=request_id, telegram_user_id=user.telegram_id
    )
    if card is None or card.card_message_id is not None:
        return
    if card.status is TelegramDeliveryStatus.AWAITING_QUALITY:
        sent = await message.answer(
            dependencies.presentation.track_card_text(card, locale, mode="first_quality"),
            reply_markup=dependencies.presentation.quality_keyboard(locale, request_id=request_id),
        )
    elif card.status is TelegramDeliveryStatus.AWAITING_ACTION and card.quality_profile is not None:
        sent = await message.answer(
            dependencies.presentation.track_card_text(card, locale),
            reply_markup=dependencies.presentation.track_card_keyboard(
                locale, request_id=request_id, quality=card.quality_profile
            ),
        )
    elif card.status is TelegramDeliveryStatus.AWAITING_TRACK_QUALITY:
        sent = await message.answer(
            dependencies.presentation.track_card_text(card, locale, mode="track_quality"),
            reply_markup=dependencies.presentation.track_quality_keyboard(
                locale, request_id=request_id
            ),
        )
    elif card.status is TelegramDeliveryStatus.DELIVERED:
        await message.answer(
            dependencies.presentation.text("bot.request_already_delivered", locale)
        )
        return
    else:
        await message.answer(dependencies.presentation.text("bot.preparing_download", locale))
        return
    await _record_card_message(sent, request_id, user.telegram_id, dependencies.requests)


def _batch_target(callback: CallbackQuery, user_id: int) -> DownloadDeliveryTarget:
    message = callback.message
    chat_id = message.chat.id if isinstance(message, Message) else user_id
    chat_type = message.chat.type if isinstance(message, Message) else "private"
    context = telegram_context_from_values(user_id, chat_id, chat_type)
    if context is None:
        context = telegram_context_from_values(user_id, user_id, "private")
    assert context is not None
    return DownloadDeliveryTarget(
        user_id=user_id,
        context=context,
        delivery_target=PrivateUserTarget(user_id),
        source_message_id=(message.message_id if isinstance(message, Message) else 1),
    )


def _message_batch_target(message: Message, user_id: int) -> DownloadDeliveryTarget:
    context = telegram_context_from_values(user_id, message.chat.id, message.chat.type)
    if context is None:
        context = telegram_context_from_values(user_id, user_id, "private")
    assert context is not None
    return DownloadDeliveryTarget(
        user_id=user_id,
        context=context,
        delivery_target=PrivateUserTarget(user_id),
        source_message_id=message.message_id,
    )


async def _render_batch(
    callback: CallbackQuery,
    dependencies: TelegramHandlerDependencies,
    batch_id: int,
    locale: str,
) -> bool:
    service = dependencies.batch_download
    if service is None:
        return False
    progress = await service.progress(batch_id)
    if progress is None:
        return False
    async with service.database.transaction() as repositories:
        batch = await repositories.batch_download.get(batch_id)
    if batch is None:
        return False
    terminal = batch.status.value in {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}
    text = dependencies.presentation.batch_progress_text(
        batch.title, progress, locale, terminal=terminal
    )
    markup = (
        dependencies.presentation.batch_progress_keyboard(locale, batch_id=batch_id, retry=True)
        if terminal and batch.status.value in {"PARTIAL", "FAILED"}
        else (
            dependencies.presentation.batch_progress_keyboard(locale, batch_id=batch_id)
            if not terminal
            else InlineKeyboardMarkup(inline_keyboard=[])
        )
    )
    await _edit_or_send(
        callback,
        text,
        markup,
    )
    return True


async def _send_album_request_card(
    message: Message,
    user: User,
    request_id: int,
    dependencies: TelegramHandlerDependencies,
) -> None:
    if dependencies.albums is None:
        return
    locale = dependencies.users.locale_for(user)
    card = await dependencies.albums.card(request_id=request_id, telegram_user_id=user.telegram_id)
    if card is None or card.card_message_id is not None:
        return
    if card.status is AlbumRequestStatus.AWAITING_QUALITY:
        sent = await message.answer(
            dependencies.presentation.album_card_text(card, locale, mode="first_quality"),
            reply_markup=dependencies.presentation.quality_keyboard(
                locale, album_request_id=request_id
            ),
        )
    elif card.status is AlbumRequestStatus.AWAITING_ACTION and card.quality_profile is not None:
        sent = await message.answer(
            dependencies.presentation.album_card_text(card, locale),
            reply_markup=dependencies.presentation.album_card_keyboard(
                locale, request_id=request_id, quality=card.quality_profile
            ),
        )
    elif card.status is AlbumRequestStatus.AWAITING_ALBUM_QUALITY:
        sent = await message.answer(
            dependencies.presentation.album_card_text(card, locale, mode="album_quality"),
            reply_markup=dependencies.presentation.album_quality_keyboard(
                locale, request_id=request_id
            ),
        )
    elif card.status is AlbumRequestStatus.SELECTING_TRACKS:
        page = await dependencies.albums.selection_page(
            request_id=request_id, telegram_user_id=user.telegram_id, page=0
        )
        if page is None:
            return
        sent = await message.answer(
            dependencies.presentation.album_selection_text(page, locale),
            reply_markup=dependencies.presentation.album_selection_keyboard(page, locale),
        )
    else:
        await message.answer(dependencies.presentation.text("bot.album_preparing", locale))
        return
    await dependencies.albums.record_card_message(
        request_id=request_id,
        telegram_user_id=user.telegram_id,
        message_id=sent.message_id,
    )


async def _render_album_card(
    callback: CallbackQuery,
    dependencies: TelegramHandlerDependencies,
    request_id: int,
    locale: str,
) -> bool:
    if dependencies.albums is None:
        return False
    card = await dependencies.albums.card(
        request_id=request_id, telegram_user_id=callback.from_user.id
    )
    if card is None or card.quality_profile is None:
        return False
    await _edit_or_send(
        callback,
        dependencies.presentation.album_card_text(card, locale),
        dependencies.presentation.album_card_keyboard(
            locale, request_id=request_id, quality=card.quality_profile
        ),
    )
    return True


async def _render_album_page(
    callback: CallbackQuery,
    dependencies: TelegramHandlerDependencies,
    request_id: int,
    page_number: int,
    locale: str,
) -> bool:
    if dependencies.albums is None:
        return False
    page = await dependencies.albums.selection_page(
        request_id=request_id,
        telegram_user_id=callback.from_user.id,
        page=page_number,
    )
    if page is None:
        return False
    await _edit_or_send(
        callback,
        dependencies.presentation.album_selection_text(page, locale),
        dependencies.presentation.album_selection_keyboard(page, locale),
    )
    return True


async def _album_bulk_selection(
    callback: CallbackQuery,
    dependencies: TelegramHandlerDependencies,
    *,
    selected: bool,
) -> None:
    user = await _observe_callback(callback, dependencies.users)
    locale = dependencies.users.locale_for(user)
    request_id = (
        parse_album_select_all(callback.data) if selected else parse_album_clear_all(callback.data)
    )
    if request_id is None or dependencies.albums is None:
        await _invalid_callback(callback, dependencies.presentation, locale)
        return
    result = await dependencies.albums.select_all(
        request_id=request_id,
        telegram_user_id=callback.from_user.id,
        selected=selected,
    )
    if not result.accepted:
        await _answer_album_failure(callback, dependencies.presentation, locale, result)
        return
    await _render_album_page(callback, dependencies, request_id, 0, locale)
    await callback.answer()


async def _answer_album_failure(
    callback: CallbackQuery,
    presentation: TelegramPresentation,
    locale: str,
    result: AlbumActionResult,
) -> None:
    key = (
        "bot.invalid_selection"
        if result.outcome in {AlbumActionOutcome.FORBIDDEN, AlbumActionOutcome.NOT_FOUND}
        else "bot.album_request_stale"
    )
    await callback.answer(presentation.text(key, locale), show_alert=True)


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
    *,
    acknowledged: bool = False,
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
    if acknowledged:
        if isinstance(callback.message, Message):
            await _edit_or_send(
                callback,
                presentation.text(key, locale),
                InlineKeyboardMarkup(inline_keyboard=[]),
            )
        return
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
