from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.enums import ChatType, MessageEntityType
from aiogram.types import Chat, Message, MessageEntity, Update
from aiogram.types import User as TgUser
from sqlalchemy import func, select

from app.core.enums import (
    AlbumRequestStatus,
    DownloadPlanOperation,
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    QualityProfile,
    TelegramDeliveryStatus,
    TelegramMediaKind,
)
from app.core.models import (
    AlbumSnapshot,
    AlbumTrackSnapshot,
    DownloadArtifactMetadata,
    TelegramUploadReceipt,
)
from app.i18n import LocalizationService
from app.providers.base import AlbumReference, TrackReference
from app.services.deep_links import DeepLinkRegistryService
from app.services.delivery import DeliveryPreparationService
from app.services.telegram_albums import TelegramAlbumRequestService
from app.services.telegram_cache import TelegramFileCacheService
from app.services.telegram_delivery import TelegramDeliveryWorker
from app.services.telegram_requests import TelegramTrackRequestService
from app.services.telegram_users import TelegramUserService
from app.storage import Database
from app.storage.models import (
    DownloadJob,
    JobSubscriber,
    TelegramAlbumRequest,
    TelegramDeliveryRequest,
    UploadJob,
)
from app.telegram.gateway import TelegramDeliveryReceipt
from app.telegram.handlers import TelegramHandlerDependencies, create_stage9_router
from app.telegram.presentation import TelegramPresentation

TRACK_URL = "https://open.spotify.com/track/0123456789012345678901"
ALBUM_URL = "https://open.spotify.com/album/0123456789012345678901"


class Provider:
    async def classify_url(self, url: str) -> TrackReference | AlbumReference:
        if url == TRACK_URL:
            return TrackReference(
                MusicProviderName.SPOTIFY,
                "0123456789012345678901",
                TRACK_URL,
            )
        return AlbumReference(
            MusicProviderName.SPOTIFY,
            "0123456789012345678901",
            ALBUM_URL,
        )


@dataclass
class RegistrationResolver:
    track_id: int
    calls: int = 0

    async def resolve(self, url: str, *, discover: bool = False) -> SimpleNamespace:
        self.calls += 1
        assert discover
        return SimpleNamespace(track=SimpleNamespace(id=self.track_id))


@dataclass
class UrlResolver:
    track_id: int
    calls: int = 0

    async def resolve_track_id(self, url: str) -> int:
        self.calls += 1
        return self.track_id


@dataclass
class AlbumTargetResolver:
    calls: int = 0

    async def resolve_album(self, url: str) -> AlbumSnapshot:
        raise AssertionError("deep-link opening must use provider Album identity")

    async def resolve_album_target(
        self, provider: MusicProviderName, provider_album_id: str
    ) -> AlbumSnapshot:
        self.calls += 1
        return AlbumSnapshot(
            provider,
            provider_album_id,
            ALBUM_URL,
            "Deep Link Album",
            "Album Artist",
            (
                AlbumTrackSnapshot(
                    provider_track_id="track-1",
                    position=1,
                    title="Track 1",
                    artist="Album Artist",
                ),
            ),
        )


@dataclass
class CacheGateway:
    sent: int = 0

    async def send_cached_audio(self, spec) -> TelegramDeliveryReceipt:  # type: ignore[no-untyped-def]
        self.sent += 1
        return TelegramDeliveryReceipt(spec.chat_id, 9001)

    async def send_cached_document(self, spec) -> TelegramDeliveryReceipt:  # type: ignore[no-untyped-def]
        return await self.send_cached_audio(spec)


async def _track(database: Database) -> int:
    async with database.transaction() as repositories:
        track = await repositories.tracks.create_track(title="Song", artist="Artist")
        return track.id


def _message_update(user_id: int, message_id: int, payload: str) -> Update:
    user = TgUser(id=user_id, is_bot=False, first_name="User", language_code="en")
    chat = Chat(id=user_id, type=ChatType.PRIVATE)
    text = f"/start {payload}"
    return Update(
        update_id=message_id,
        message=Message(
            message_id=message_id,
            date=datetime.now(UTC),
            chat=chat,
            from_user=user,
            text=text,
            entities=[MessageEntity(type=MessageEntityType.BOT_COMMAND, offset=0, length=6)],
        ),
    )


def _sent_message(user_id: int, message_id: int = 9000) -> Message:
    user = TgUser(id=user_id, is_bot=False, first_name="Bot")
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type=ChatType.PRIVATE),
        from_user=user,
        text="card",
    )


async def test_track_deep_link_uses_local_track_card_and_survives_later_revocation(
    database: Database,
) -> None:
    track_id = await _track(database)
    registration_resolver = RegistrationResolver(track_id)
    registry = DeepLinkRegistryService(
        database,
        Provider(),  # type: ignore[arg-type]
        registration_resolver,  # type: ignore[arg-type]
        telegram_bot_id=100,
    )
    token = (await registry.register_from_url(TRACK_URL)).entry.token
    url_resolver = UrlResolver(track_id)
    i18n = LocalizationService(("en", "ru"), "en")
    users = TelegramUserService(database, i18n, owner_id=None)
    async with database.transaction() as repositories:
        await repositories.users.create_user(501, preferred_quality_profile=QualityProfile.MP3_320)
    requests = TelegramTrackRequestService(
        database,
        url_resolver,
        telegram_bot_id=100,  # type: ignore[arg-type]
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_stage9_router(
            TelegramHandlerDependencies(
                users,
                requests,
                TelegramPresentation(i18n),
                deep_links=registry,
            )
        )
    )
    bot = Bot("123456:TEST_TOKEN")
    try:
        with patch.object(Bot, "__call__", new_callable=AsyncMock, return_value=_sent_message(501)):
            update = _message_update(501, 1, token)
            await dispatcher.feed_update(bot, update)
            await dispatcher.feed_update(bot, update)
            await registry.revoke(token)
            await dispatcher.feed_update(bot, _message_update(501, 2, token))
    finally:
        await bot.session.close()

    assert registration_resolver.calls == 1
    assert url_resolver.calls == 0
    async with database.transaction() as repositories:
        first = await repositories.telegram_delivery.get_by_message(
            telegram_bot_id=100, telegram_chat_id=501, source_message_id=1
        )
        revoked_open = await repositories.telegram_delivery.get_by_message(
            telegram_bot_id=100, telegram_chat_id=501, source_message_id=2
        )
        counts = await repositories.download_jobs.counts()
    assert first is not None
    assert first.track_id == track_id
    assert first.status is TelegramDeliveryStatus.AWAITING_ACTION
    assert revoked_open is None
    assert counts == {}
    assert (
        await requests.start_default_quality(request_id=first.id, telegram_user_id=501)
    ).accepted


async def test_album_deep_link_materializes_user_snapshot_only_when_opened(
    database: Database,
) -> None:
    track_id = await _track(database)
    registry = DeepLinkRegistryService(
        database,
        Provider(),  # type: ignore[arg-type]
        RegistrationResolver(track_id),  # type: ignore[arg-type]
        telegram_bot_id=100,
    )
    token = (await registry.register_from_url(ALBUM_URL)).entry.token
    album_resolver = AlbumTargetResolver()
    i18n = LocalizationService(("en", "ru"), "en")
    users = TelegramUserService(database, i18n, owner_id=None)
    requests = TelegramTrackRequestService(
        database,
        UrlResolver(track_id),
        telegram_bot_id=100,  # type: ignore[arg-type]
    )
    albums = TelegramAlbumRequestService(
        database,
        album_resolver,  # type: ignore[arg-type]
        telegram_bot_id=100,
    )
    async with database.engine.connect() as connection:
        before = await connection.scalar(select(func.count(TelegramAlbumRequest.id)))
    assert before == 0

    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_stage9_router(
            TelegramHandlerDependencies(
                users,
                requests,
                TelegramPresentation(i18n),
                albums=albums,
                deep_links=registry,
            )
        )
    )
    bot = Bot("123456:TEST_TOKEN")
    try:
        with patch.object(Bot, "__call__", new_callable=AsyncMock, return_value=_sent_message(601)):
            await dispatcher.feed_update(bot, _message_update(601, 10, token))
    finally:
        await bot.session.close()

    assert album_resolver.calls == 1
    async with database.transaction() as repositories:
        album = await repositories.telegram_album.get_by_message(
            telegram_bot_id=100, telegram_chat_id=601, source_message_id=10
        )
        session = repositories.telegram_album._session  # noqa: SLF001
        download_count = await session.scalar(select(func.count(DownloadJob.id)))
        upload_count = await session.scalar(select(func.count(UploadJob.id)))
        delivery_count = await session.scalar(select(func.count(TelegramDeliveryRequest.id)))
    assert album is not None
    assert album.status is AlbumRequestStatus.AWAITING_QUALITY
    assert download_count == upload_count == delivery_count == 0


async def test_one_hundred_users_share_existing_singleflight_after_deep_link_actions(
    database: Database,
) -> None:
    track_id = await _track(database)
    registry = DeepLinkRegistryService(
        database,
        Provider(),  # type: ignore[arg-type]
        RegistrationResolver(track_id),  # type: ignore[arg-type]
        telegram_bot_id=100,
    )
    token = (await registry.register_from_url(TRACK_URL)).entry.token
    requests = TelegramTrackRequestService(
        database,
        UrlResolver(track_id),
        telegram_bot_id=100,  # type: ignore[arg-type]
    )
    for index in range(100):
        telegram_id = 10_000 + index
        async with database.transaction() as repositories:
            user = await repositories.users.create_user(
                telegram_id, preferred_quality_profile=QualityProfile.MP3_320
            )
        target = await registry.resolve_start_payload(token)
        assert target is not None and target.track_id == track_id
        request = await requests.request_track_id(
            user=user,
            telegram_chat_id=telegram_id,
            source_message_id=1,
            track_id=target.track_id,
        )
        assert (
            await requests.start_default_quality(
                request_id=request.id, telegram_user_id=telegram_id
            )
        ).accepted

    worker = TelegramDeliveryWorker(
        database,
        DeliveryPreparationService(database, telegram_bot_id=100, max_size=1000),
        TelegramFileCacheService(database),
        object(),  # type: ignore[arg-type]
        LocalizationService(("en", "ru"), "en"),
        max_attempts=3,
        wake_event=asyncio.Event(),
    )
    for index in range(100):
        request = await worker.claim(f"stage11-delivery-{index}")
        assert request is not None
        await worker.process(request, f"stage11-delivery-{index}")

    async with database.transaction() as repositories:
        session = repositories.singleflight._session  # noqa: SLF001
        jobs = await session.scalar(select(func.count(DownloadJob.id)))
        subscribers = await session.scalar(select(func.count(JobSubscriber.id)))
    assert jobs == 1
    assert subscribers == 100


async def test_deep_link_action_delivers_seeded_cache_without_provider_work(
    database: Database,
) -> None:
    track_id = await _track(database)
    registration_resolver = RegistrationResolver(track_id)
    registry = DeepLinkRegistryService(
        database,
        Provider(),  # type: ignore[arg-type]
        registration_resolver,  # type: ignore[arg-type]
        telegram_bot_id=100,
    )
    token = (await registry.register_from_url(TRACK_URL)).entry.token
    cache = TelegramFileCacheService(database)
    await cache.upsert_success(
        track_id=track_id,
        quality_profile=QualityProfile.MP3_320,
        receipt=TelegramUploadReceipt(
            100,
            -100123,
            1,
            TelegramMediaKind.AUDIO,
            "cached-file-id",
            "cached-unique-id",
            1234,
        ),
        artifact=DownloadArtifactMetadata(
            None,
            MusicProviderName.SPOTIFY,
            "source-id",
            DownloadPlanOperation.DIRECT,
            False,
            NativeCodec.MP3,
            NativeContainer.MP3,
            320,
            NativeCodec.MP3,
            NativeContainer.MP3,
            320,
            None,
            None,
            None,
            180_000,
            1234,
        ),
    )
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(
            700, preferred_quality_profile=QualityProfile.MP3_320
        )
    target = await registry.resolve_start_payload(token)
    assert target is not None and target.track_id is not None
    requests = TelegramTrackRequestService(
        database,
        UrlResolver(track_id),
        telegram_bot_id=100,  # type: ignore[arg-type]
    )
    request = await requests.request_track_id(
        user=user,
        telegram_chat_id=700,
        source_message_id=1,
        track_id=target.track_id,
    )
    assert (
        await requests.start_default_quality(request_id=request.id, telegram_user_id=700)
    ).accepted
    gateway = CacheGateway()
    worker = TelegramDeliveryWorker(
        database,
        DeliveryPreparationService(database, telegram_bot_id=100, max_size=1000),
        cache,
        gateway,  # type: ignore[arg-type]
        LocalizationService(("en", "ru"), "en"),
        max_attempts=3,
        wake_event=asyncio.Event(),
    )
    for index in range(2):
        claimed = await worker.claim(f"cache-delivery-{index}")
        assert claimed is not None
        await worker.process(claimed, f"cache-delivery-{index}")
    assert gateway.sent == 1
    assert registration_resolver.calls == 1
    async with database.transaction() as repositories:
        session = repositories.singleflight._session  # noqa: SLF001
        assert await session.scalar(select(func.count(DownloadJob.id))) == 0
        assert await session.scalar(select(func.count(JobSubscriber.id))) == 0
