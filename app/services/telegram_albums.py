"""Durable album admission, quality actions, and persistent selection state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum

from app.core.enums import AlbumRequestStatus, BatchSourceType, MusicProviderName, QualityProfile
from app.core.exceptions import DatabaseConcurrencyError
from app.core.models import AlbumSnapshot, ResolvedCollection, ResolvedCollectionItem
from app.providers.base import MusicProvider
from app.storage import Database
from app.storage.database import Repositories
from app.storage.models import TelegramAlbumItem, TelegramAlbumRequest, User
from app.storage.models.base import utc_now

ALBUM_TRACKS_PER_PAGE = 8


class TelegramAlbumResolver:
    def __init__(self, provider: MusicProvider) -> None:
        self._provider = provider

    async def resolve_album(self, url: str) -> AlbumSnapshot:
        return await self._provider.get_album(url)

    async def resolve_album_target(
        self, provider: MusicProviderName, provider_album_id: str
    ) -> AlbumSnapshot:
        return await self._provider.get_album_by_id(provider, provider_album_id)


class AlbumActionOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    EMPTY = "EMPTY"
    STALE = "STALE"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True, slots=True)
class AlbumActionResult:
    request: TelegramAlbumRequest | None
    outcome: AlbumActionOutcome

    @property
    def accepted(self) -> bool:
        return self.outcome is AlbumActionOutcome.ACCEPTED


@dataclass(frozen=True, slots=True)
class AlbumCard:
    request_id: int
    status: AlbumRequestStatus
    quality_profile: QualityProfile | None
    artist: str
    title: str
    release_date: str | None
    duration_ms: int | None
    track_count: int
    card_message_id: int | None


@dataclass(frozen=True, slots=True)
class AlbumSelectionPage:
    card: AlbumCard
    items: tuple[TelegramAlbumItem, ...]
    page: int
    page_count: int
    selected_count: int


class TelegramAlbumRequestService:
    def __init__(
        self,
        database: Database,
        resolver: TelegramAlbumResolver,
        *,
        telegram_bot_id: int,
        wake_event: asyncio.Event | None = None,
    ) -> None:
        self._database = database
        self._resolver = resolver
        self._telegram_bot_id = telegram_bot_id
        self._wake_event = wake_event

    async def request_album(
        self,
        *,
        user: User,
        telegram_chat_id: int,
        source_message_id: int,
        url: str,
    ) -> TelegramAlbumRequest:
        existing = await self._get_by_message(telegram_chat_id, source_message_id)
        if existing is not None:
            return existing
        snapshot = await self._resolver.resolve_album(url)
        return await self._persist_snapshot(
            user=user,
            telegram_chat_id=telegram_chat_id,
            source_message_id=source_message_id,
            snapshot=snapshot,
        )

    async def request_album_target(
        self,
        *,
        user: User,
        telegram_chat_id: int,
        source_message_id: int,
        provider: MusicProviderName,
        provider_album_id: str,
    ) -> TelegramAlbumRequest:
        existing = await self._get_by_message(telegram_chat_id, source_message_id)
        if existing is not None:
            return existing
        snapshot = await self._resolver.resolve_album_target(provider, provider_album_id)
        return await self._persist_snapshot(
            user=user,
            telegram_chat_id=telegram_chat_id,
            source_message_id=source_message_id,
            snapshot=snapshot,
        )

    async def _persist_snapshot(
        self,
        *,
        user: User,
        telegram_chat_id: int,
        source_message_id: int,
        snapshot: AlbumSnapshot,
    ) -> TelegramAlbumRequest:
        try:
            async with self._database.transaction() as repositories:
                existing = await repositories.telegram_album.get_by_message(
                    telegram_bot_id=self._telegram_bot_id,
                    telegram_chat_id=telegram_chat_id,
                    source_message_id=source_message_id,
                )
                if existing is not None:
                    return existing
                stored_user = await repositories.users.get(user.id)
                if stored_user is None:
                    raise ValueError("Telegram user disappeared during album admission")
                return await repositories.telegram_album.create(
                    telegram_bot_id=self._telegram_bot_id,
                    user_id=user.id,
                    telegram_chat_id=telegram_chat_id,
                    source_message_id=source_message_id,
                    snapshot=snapshot,
                    quality_profile=stored_user.preferred_quality_profile,
                    now=utc_now(),
                )
        except DatabaseConcurrencyError:
            recovered = await self._get_by_message(telegram_chat_id, source_message_id)
            if recovered is None:
                raise
            return recovered

    async def card(self, *, request_id: int, telegram_user_id: int) -> AlbumCard | None:
        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(telegram_user_id)
            request = await repositories.telegram_album.get(request_id)
            if user is None or request is None or request.user_id != user.id:
                return None
            return _card(request)

    async def collection(
        self, *, request_id: int, telegram_user_id: int
    ) -> ResolvedCollection | None:
        """Return the durable album snapshot as a batch collection for its owner."""
        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(telegram_user_id)
            request = await repositories.telegram_album.get(request_id)
            if user is None or request is None or request.user_id != user.id:
                return None
            items = await repositories.telegram_album.list_items(request_id, offset=0, limit=500)
            return ResolvedCollection(
                source_type=BatchSourceType.ALBUM,
                provider=request.provider,
                collection_id=request.provider_album_id,
                source_reference=request.provider_album_id,
                title=request.title,
                creator=request.artist,
                items=tuple(
                    ResolvedCollectionItem(
                        position=item.position,
                        provider_media_id=item.provider_track_id,
                        title=item.title,
                        artist=item.artist,
                        duration_ms=item.duration_ms,
                    )
                    for item in items
                ),
            )

    async def selection_page(
        self, *, request_id: int, telegram_user_id: int, page: int
    ) -> AlbumSelectionPage | None:
        if page < 0:
            return None
        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(telegram_user_id)
            request = await repositories.telegram_album.get(request_id)
            if user is None or request is None or request.user_id != user.id:
                return None
            if request.status is not AlbumRequestStatus.SELECTING_TRACKS:
                return None
            page_count = max(
                (request.track_count + ALBUM_TRACKS_PER_PAGE - 1) // ALBUM_TRACKS_PER_PAGE, 1
            )
            if page >= page_count:
                return None
            items = await repositories.telegram_album.list_items(
                request_id,
                offset=page * ALBUM_TRACKS_PER_PAGE,
                limit=ALBUM_TRACKS_PER_PAGE,
            )
            selected = await repositories.telegram_album.count_selected(request_id)
            return AlbumSelectionPage(_card(request), tuple(items), page, page_count, selected)

    async def record_card_message(
        self, *, request_id: int, telegram_user_id: int, message_id: int
    ) -> bool:
        if message_id <= 0:
            return False
        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(telegram_user_id)
            if user is None:
                return False
            return await repositories.telegram_album.record_card_message(
                request_id=request_id, user_id=user.id, message_id=message_id
            )

    async def choose_first_quality(
        self,
        *,
        request_id: int,
        telegram_user_id: int,
        quality_profile: QualityProfile,
    ) -> AlbumActionResult:
        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(telegram_user_id)
            if user is None:
                return AlbumActionResult(None, AlbumActionOutcome.NOT_FOUND)
            changed = await repositories.telegram_album.choose_first_quality(
                request_id=request_id, user_id=user.id, quality=quality_profile
            )
            if changed is not None:
                await repositories.users.set_preferred_quality(user, quality_profile)
                return AlbumActionResult(changed, AlbumActionOutcome.ACCEPTED)
            return await self._failure(repositories, request_id, user.id)

    async def download_all(self, *, request_id: int, telegram_user_id: int) -> AlbumActionResult:
        result = await self._action(request_id, telegram_user_id, "queue_all")
        if result.accepted:
            self.wake()
        return result

    async def open_selection(self, *, request_id: int, telegram_user_id: int) -> AlbumActionResult:
        return await self._action(request_id, telegram_user_id, "open_selection")

    async def back_from_selection(
        self, *, request_id: int, telegram_user_id: int
    ) -> AlbumActionResult:
        return await self._action(request_id, telegram_user_id, "selection_back")

    async def open_quality(self, *, request_id: int, telegram_user_id: int) -> AlbumActionResult:
        return await self._action(request_id, telegram_user_id, "open_quality")

    async def choose_quality(
        self,
        *,
        request_id: int,
        telegram_user_id: int,
        quality_profile: QualityProfile,
    ) -> AlbumActionResult:
        return await self._action(request_id, telegram_user_id, "choose_quality", quality_profile)

    async def back_from_quality(
        self, *, request_id: int, telegram_user_id: int
    ) -> AlbumActionResult:
        return await self._action(request_id, telegram_user_id, "quality_back")

    async def toggle(
        self, *, request_id: int, item_id: int, telegram_user_id: int
    ) -> AlbumActionResult:
        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(telegram_user_id)
            if user is None:
                return AlbumActionResult(None, AlbumActionOutcome.NOT_FOUND)
            if await repositories.telegram_album.toggle_item(
                request_id=request_id, item_id=item_id, user_id=user.id
            ):
                return AlbumActionResult(
                    await repositories.telegram_album.get(request_id),
                    AlbumActionOutcome.ACCEPTED,
                )
            return await self._failure(repositories, request_id, user.id)

    async def select_all(
        self, *, request_id: int, telegram_user_id: int, selected: bool
    ) -> AlbumActionResult:
        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(telegram_user_id)
            if user is None:
                return AlbumActionResult(None, AlbumActionOutcome.NOT_FOUND)
            if await repositories.telegram_album.set_all_selected(
                request_id=request_id, user_id=user.id, selected=selected
            ):
                return AlbumActionResult(
                    await repositories.telegram_album.get(request_id),
                    AlbumActionOutcome.ACCEPTED,
                )
            return await self._failure(repositories, request_id, user.id)

    async def download_selected(
        self, *, request_id: int, telegram_user_id: int
    ) -> AlbumActionResult:
        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(telegram_user_id)
            if user is None:
                return AlbumActionResult(None, AlbumActionOutcome.NOT_FOUND)
            changed, empty = await repositories.telegram_album.queue_selected(
                request_id=request_id, user_id=user.id, now=utc_now()
            )
            if changed is not None:
                result = AlbumActionResult(changed, AlbumActionOutcome.ACCEPTED)
            elif empty:
                current = await repositories.telegram_album.get(request_id)
                if current is not None and current.user_id != user.id:
                    result = AlbumActionResult(current, AlbumActionOutcome.FORBIDDEN)
                elif current is not None and current.status is AlbumRequestStatus.SELECTING_TRACKS:
                    result = AlbumActionResult(current, AlbumActionOutcome.EMPTY)
                else:
                    result = await self._failure(repositories, request_id, user.id)
            else:
                result = await self._failure(repositories, request_id, user.id)
        if result.accepted:
            self.wake()
        return result

    async def _action(
        self,
        request_id: int,
        telegram_user_id: int,
        action: str,
        quality: QualityProfile | None = None,
    ) -> AlbumActionResult:
        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(telegram_user_id)
            if user is None:
                return AlbumActionResult(None, AlbumActionOutcome.NOT_FOUND)
            repository = repositories.telegram_album
            if action == "queue_all":
                changed = await repository.queue_all(
                    request_id=request_id, user_id=user.id, now=utc_now()
                )
            elif action == "open_selection":
                changed = await repository.open_selection(request_id=request_id, user_id=user.id)
            elif action == "selection_back":
                changed = await repository.selection_back(request_id=request_id, user_id=user.id)
            elif action == "open_quality":
                changed = await repository.open_quality(request_id=request_id, user_id=user.id)
            elif action == "choose_quality" and quality is not None:
                changed = await repository.choose_quality(
                    request_id=request_id, user_id=user.id, quality=quality
                )
            elif action == "quality_back":
                changed = await repository.quality_back(request_id=request_id, user_id=user.id)
            else:
                raise ValueError("invalid album action")
            if changed is not None:
                return AlbumActionResult(changed, AlbumActionOutcome.ACCEPTED)
            return await self._failure(repositories, request_id, user.id)

    async def _get_by_message(
        self, telegram_chat_id: int, source_message_id: int
    ) -> TelegramAlbumRequest | None:
        async with self._database.transaction() as repositories:
            return await repositories.telegram_album.get_by_message(
                telegram_bot_id=self._telegram_bot_id,
                telegram_chat_id=telegram_chat_id,
                source_message_id=source_message_id,
            )

    @staticmethod
    async def _failure(
        repositories: Repositories, request_id: int, user_id: int
    ) -> AlbumActionResult:
        current = await repositories.telegram_album.get(request_id)
        if current is None:
            outcome = AlbumActionOutcome.NOT_FOUND
        elif current.user_id != user_id:
            outcome = AlbumActionOutcome.FORBIDDEN
        else:
            outcome = AlbumActionOutcome.STALE
        return AlbumActionResult(current, outcome)

    def wake(self) -> None:
        if self._wake_event is not None:
            self._wake_event.set()


def _card(request: TelegramAlbumRequest) -> AlbumCard:
    return AlbumCard(
        request.id,
        request.status,
        request.quality_profile,
        request.artist,
        request.title,
        request.release_date,
        request.duration_ms,
        request.track_count,
        request.card_message_id,
    )
