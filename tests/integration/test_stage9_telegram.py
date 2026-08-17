from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.enums import ChatType, MessageEntityType
from aiogram.types import CallbackQuery, Chat, Message, MessageEntity, Update
from aiogram.types import User as TgUser
from sqlalchemy import func, select, update

from app.composition import compose_stage9
from app.config import Settings
from app.core.enums import (
    DownloadPlanOperation,
    DownloadPlanReadiness,
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    QualityProfile,
    TelegramDeliveryStatus,
    TelegramMediaKind,
)
from app.core.models import (
    DownloadResult,
    PreparedSourceMedia,
    TelegramBotIdentity,
    TelegramUploadReceipt,
)
from app.i18n import LocalizationService
from app.services.artifacts import DownloadArtifactManager
from app.services.delivery import DeliveryPreparationService
from app.services.queues import UploadQueueService
from app.services.singleflight import SubscriberNotifier
from app.services.telegram_cache import TelegramFileCacheService
from app.services.telegram_delivery import TelegramDeliveryWorker
from app.services.telegram_requests import TelegramTrackRequestService
from app.services.telegram_upload import TelegramCacheUploadExecutor
from app.services.telegram_users import TelegramUserProfile, TelegramUserService
from app.services.workers import DownloadWorkerBackend, UploadWorkerBackend
from app.storage import Database
from app.storage.models import DownloadJob, JobSubscriber, TelegramDeliveryRequest, UploadJob
from app.storage.models.base import utc_now
from app.telegram import (
    TelegramCachedMediaSpec,
    TelegramDeliveryReceipt,
    TelegramGatewayError,
    TelegramUploadSpec,
)
from app.telegram.handlers import TelegramHandlerDependencies, create_stage9_router
from app.telegram.presentation import TelegramPresentation, encode_first_quality


@dataclass
class Resolver:
    track_id: int
    calls: int = 0

    async def resolve_track_id(self, url: str) -> int:
        self.calls += 1
        return self.track_id


class Gateway:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.uploads = 0
        self.error: TelegramGatewayError | None = None
        self.message_id = 10

    async def get_bot_identity(self) -> TelegramBotIdentity:
        return TelegramBotIdentity(100, "stage9_bot")

    async def upload_audio(self, spec: TelegramUploadSpec) -> TelegramUploadReceipt:
        self.uploads += 1
        self.message_id += 1
        return TelegramUploadReceipt(
            100,
            -100123,
            self.message_id,
            TelegramMediaKind.AUDIO,
            "shared-file-id",
            "shared-unique-id",
            spec.file_path.stat().st_size,
        )

    async def upload_document(self, spec: TelegramUploadSpec) -> TelegramUploadReceipt:
        return await self.upload_audio(spec)

    async def send_cached_audio(self, spec: TelegramCachedMediaSpec) -> TelegramDeliveryReceipt:
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        self.sent.append(spec.file_id)
        self.message_id += 1
        return TelegramDeliveryReceipt(spec.chat_id, self.message_id)

    async def send_cached_document(self, spec: TelegramCachedMediaSpec) -> TelegramDeliveryReceipt:
        return await self.send_cached_audio(spec)

    async def send_text(self, chat_id: int, text: str) -> TelegramDeliveryReceipt:
        self.message_id += 1
        return TelegramDeliveryReceipt(chat_id, self.message_id)

    async def close(self) -> None:
        return None


class IdleProvider:
    closed = False

    async def close(self) -> None:
        self.closed = True


@dataclass
class Pipeline:
    artifacts: DownloadArtifactManager
    source_id: int
    calls: int = 0

    async def download(self, track_id: int, quality_profile: QualityProfile) -> DownloadResult:
        self.calls += 1
        job_id, _ = self.artifacts.create_job()
        path = self.artifacts.final_path(job_id, "mp3")
        path.write_bytes(b"stage-9-audio")
        source = PreparedSourceMedia(
            MusicProviderName.QOBUZ,
            "qobuz-source",
            codec=NativeCodec.FLAC,
            container=NativeContainer.FLAC,
            bitrate_kbps=1000,
            lossless=True,
        )
        output = PreparedSourceMedia(
            MusicProviderName.QOBUZ,
            "qobuz-source",
            codec=NativeCodec.MP3,
            container=NativeContainer.MP3,
            bitrate_kbps=320,
            lossless=False,
            file_path=path,
        )
        return DownloadResult(
            job_id,
            track_id,
            quality_profile,
            self.source_id,
            MusicProviderName.QOBUZ,
            "qobuz-source",
            DownloadPlanOperation.TRANSCODE,
            DownloadPlanReadiness.CONFIRMED,
            source,
            output,
            path,
            path.stat().st_size,
            True,
            0,
            (),
            datetime.now(UTC),
            "libmp3lame",
        )


async def _track(database: Database) -> tuple[int, int]:
    async with database.transaction() as repositories:
        track = await repositories.tracks.create_track(title="Song", artist="Artist")
        source = await repositories.track_sources.upsert_source(
            track_id=track.id,
            provider=MusicProviderName.QOBUZ,
            provider_track_id="qobuz-source",
            url="https://example.test/track",
        )
        return track.id, source.source.id


async def _user(database: Database, telegram_id: int, quality: QualityProfile | None):
    async with database.transaction() as repositories:
        return await repositories.users.create_user(telegram_id, preferred_quality_profile=quality)


def _worker(
    database: Database,
    gateway: Gateway,
    wake: asyncio.Event,
) -> TelegramDeliveryWorker:
    cache = TelegramFileCacheService(database)
    return TelegramDeliveryWorker(
        database,
        DeliveryPreparationService(database, telegram_bot_id=100, max_size=1000),
        cache,
        gateway,
        LocalizationService(("en", "ru"), "en"),
        max_attempts=3,
        wake_event=wake,
    )


async def _drain(worker: TelegramDeliveryWorker, count: int) -> None:
    for index in range(count):
        request = await worker.claim(f"delivery-{index}")
        assert request is not None
        await worker.process(request, f"delivery-{index}")


async def test_user_observation_preserves_manual_locale_and_reconciles_owner(
    database: Database,
) -> None:
    i18n = LocalizationService(("en", "ru"), "en")
    users = TelegramUserService(database, i18n, owner_id=500)
    first = await users.observe(TelegramUserProfile(500, "owner", "Owner", "ru-RU"))
    await users.set_locale(500, "en")
    second = await users.observe(TelegramUserProfile(500, "new", "Owner", "ru"))
    assert first.role.value == "OWNER"
    assert second.telegram_language_code == "ru"
    assert second.preferred_locale == "en"
    assert users.locale_for(second) == "en"


async def test_first_quality_is_restart_safe_owned_and_idempotent(database: Database) -> None:
    track_id, _ = await _track(database)
    user = await _user(database, 501, None)
    resolver = Resolver(track_id)
    first = TelegramTrackRequestService(database, resolver, telegram_bot_id=100)
    request = await first.request_track(
        user=user,
        telegram_chat_id=501,
        source_message_id=77,
        url="https://example.test/track",
    )
    assert request.status is TelegramDeliveryStatus.AWAITING_QUALITY
    async with database.transaction() as repositories:
        assert await repositories.download_jobs.counts() == {}

    restarted = TelegramTrackRequestService(database, resolver, telegram_bot_id=100)
    hijack = await restarted.choose_first_quality(
        request_id=request.id,
        telegram_user_id=999,
        quality_profile=QualityProfile.MP3_320,
    )
    assert not hijack.accepted
    selected = await restarted.choose_first_quality(
        request_id=request.id,
        telegram_user_id=501,
        quality_profile=QualityProfile.MP3_320,
    )
    assert selected.accepted
    repeated = await restarted.choose_first_quality(
        request_id=request.id,
        telegram_user_id=501,
        quality_profile=QualityProfile.LOSSLESS,
    )
    assert not repeated.accepted
    duplicate = await restarted.request_track(
        user=user,
        telegram_chat_id=501,
        source_message_id=77,
        url="https://example.test/track",
    )
    assert duplicate.id == request.id
    assert resolver.calls == 1
    async with database.transaction() as repositories:
        stored = await repositories.users.get_by_telegram_id(501)
        assert stored is not None
        assert stored.preferred_quality_profile is QualityProfile.MP3_320


async def test_100_user_fanout_and_second_wave_cache_reuse(
    database: Database, tmp_path: Path
) -> None:
    track_id, source_id = await _track(database)
    resolver = Resolver(track_id)
    wake = asyncio.Event()
    requests = TelegramTrackRequestService(database, resolver, telegram_bot_id=100, wake_event=wake)
    for index in range(100):
        user = await _user(database, 1000 + index, QualityProfile.MP3_320)
        await requests.request_track(
            user=user,
            telegram_chat_id=1000 + index,
            source_message_id=1,
            url="https://example.test/track",
        )
    gateway = Gateway()
    delivery_worker = _worker(database, gateway, wake)
    await _drain(delivery_worker, 100)
    async with database.transaction() as repositories:
        session = repositories.singleflight._session  # noqa: SLF001
        assert await session.scalar(select(func.count(DownloadJob.id))) == 1
        assert await session.scalar(select(func.count(JobSubscriber.id))) == 100

    artifacts = DownloadArtifactManager(tmp_path / "artifacts")
    notifier = SubscriberNotifier()
    uploads = UploadQueueService(database, artifacts, subscriber_notifier=notifier)
    pipeline = Pipeline(artifacts, source_id)
    downloader = DownloadWorkerBackend(database, pipeline, artifacts, subscriber_notifier=notifier)
    download = await downloader.claim("download-1")
    assert download is not None
    await downloader.process(download, "download-1")
    uploader = UploadWorkerBackend(
        database,
        TelegramCacheUploadExecutor(
            database, TelegramFileCacheService(database), gateway, cache_chat_id=-100123
        ),
        uploads,
        subscriber_notifier=notifier,
    )
    upload = await uploader.claim("upload-1")
    assert upload is not None
    await uploader.process(upload, "upload-1")
    await _drain(delivery_worker, 100)
    assert pipeline.calls == 1
    assert gateway.uploads == 1
    assert gateway.sent == ["shared-file-id"] * 100

    for index in range(100):
        user = await _user(database, 2000 + index, QualityProfile.MP3_320)
        await requests.request_track(
            user=user,
            telegram_chat_id=2000 + index,
            source_message_id=1,
            url="https://example.test/track",
        )
    await _drain(delivery_worker, 100)
    await _drain(delivery_worker, 100)
    assert gateway.sent == ["shared-file-id"] * 200
    async with database.transaction() as repositories:
        session = repositories.singleflight._session  # noqa: SLF001
        assert await session.scalar(select(func.count(DownloadJob.id))) == 1
        assert await session.scalar(select(func.count(JobSubscriber.id))) == 100
        assert await session.scalar(select(func.count(UploadJob.id))) == 1


async def test_retry_and_invalid_file_repair_are_persistent(database: Database) -> None:
    track_id, source_id = await _track(database)
    cache = TelegramFileCacheService(database)
    gateway = Gateway()
    artifacts = DownloadArtifactManager(Path("temp") / "stage9-test-artifacts")
    pipeline = Pipeline(artifacts, source_id)
    result = await pipeline.download(track_id, QualityProfile.MP3_320)
    from app.services.workers import _artifact_metadata  # noqa: PLC0415

    await cache.upsert_success(
        track_id=track_id,
        quality_profile=QualityProfile.MP3_320,
        receipt=await gateway.upload_audio(
            TelegramUploadSpec(-100123, result.file_path, "track.mp3")
        ),
        artifact=_artifact_metadata(result),
    )
    user = await _user(database, 600, QualityProfile.MP3_320)
    service = TelegramTrackRequestService(database, Resolver(track_id), telegram_bot_id=100)
    await service.request_track(
        user=user,
        telegram_chat_id=600,
        source_message_id=1,
        url="https://example.test/track",
    )
    worker = _worker(database, gateway, asyncio.Event())
    await _drain(worker, 1)
    gateway.error = TelegramGatewayError("TEMPORARY", retryable=True)
    await _drain(worker, 1)
    async with database.engine.begin() as connection:
        await connection.execute(
            update(TelegramDeliveryRequest)
            .where(TelegramDeliveryRequest.telegram_chat_id == 600)
            .values(available_at=utc_now())
        )
    await _drain(worker, 1)
    assert gateway.sent[-1] == "shared-file-id"

    user2 = await _user(database, 601, QualityProfile.MP3_320)
    await service.request_track(
        user=user2,
        telegram_chat_id=601,
        source_message_id=1,
        url="https://example.test/track",
    )
    await _drain(worker, 1)
    gateway.error = TelegramGatewayError("BAD_FILE", retryable=False, invalid_cached_file=True)
    await _drain(worker, 1)
    async with database.transaction() as repositories:
        repaired = await repositories.telegram_delivery.get_by_message(
            telegram_bot_id=100, telegram_chat_id=601, source_message_id=1
        )
        assert repaired is not None
        assert repaired.repair_count == 1
        assert repaired.status is TelegramDeliveryStatus.QUEUED
        cached = await repositories.telegram_cache.get_active(
            telegram_bot_id=100,
            track_id=track_id,
            quality_profile=QualityProfile.MP3_320,
        )
        assert cached is None

    await _drain(worker, 1)
    notifier = SubscriberNotifier()
    uploads = UploadQueueService(database, artifacts, subscriber_notifier=notifier)
    downloader = DownloadWorkerBackend(database, pipeline, artifacts, subscriber_notifier=notifier)
    repair_download = await downloader.claim("repair-download")
    assert repair_download is not None
    await downloader.process(repair_download, "repair-download")
    uploader = UploadWorkerBackend(
        database,
        TelegramCacheUploadExecutor(database, cache, gateway, cache_chat_id=-100123),
        uploads,
        subscriber_notifier=notifier,
    )
    repair_upload = await uploader.claim("repair-upload")
    assert repair_upload is not None
    await uploader.process(repair_upload, "repair-upload")
    await _drain(worker, 1)
    async with database.transaction() as repositories:
        repaired = await repositories.telegram_delivery.get_by_message(
            telegram_bot_id=100, telegram_chat_id=601, source_message_id=1
        )
        assert repaired is not None
        assert repaired.status is TelegramDeliveryStatus.DELIVERED
        assert repaired.repair_count == 1
    artifacts.release(result.job_id)


async def test_runtime_composition_starts_and_stops_without_leaked_tasks(
    database: Database, tmp_path: Path
) -> None:
    provider = IdleProvider()
    settings = Settings(
        database_url=database.url,
        temp_dir=tmp_path / "runtime-artifacts",
        bot_token="123456:TEST_TOKEN",
        telegram_cache_chat_id=-100123,
        download_workers_default=1,
        upload_workers_default=1,
        telegram_delivery_workers=2,
    )
    components = await compose_stage9(  # type: ignore[arg-type]
        database, settings, provider, gateway=Gateway()
    )
    assert components.dispatcher.sub_routers
    await components.start()
    await asyncio.sleep(0)
    await components.stop()
    assert provider.closed


async def test_aiogram_commands_text_and_callbacks_without_network(database: Database) -> None:
    track_id, _ = await _track(database)
    i18n = LocalizationService(("en", "ru"), "en")
    users = TelegramUserService(database, i18n, owner_id=None)
    requests = TelegramTrackRequestService(database, Resolver(track_id), telegram_bot_id=100)
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_stage9_router(
            TelegramHandlerDependencies(users, requests, TelegramPresentation(i18n))
        )
    )
    bot = Bot("123456:TEST_TOKEN")
    telegram_user = TgUser(id=700, is_bot=False, first_name="User", language_code="en")
    chat = Chat(id=700, type=ChatType.PRIVATE)

    def message_update(update_id: int, text: str) -> Update:
        entities = (
            [
                MessageEntity(
                    type=MessageEntityType.BOT_COMMAND,
                    offset=0,
                    length=len(text.split()[0]),
                )
            ]
            if text.startswith("/")
            else None
        )
        return Update(
            update_id=update_id,
            message=Message(
                message_id=update_id,
                date=datetime.now(UTC),
                chat=chat,
                from_user=telegram_user,
                text=text,
                entities=entities,
            ),
        )

    try:
        with patch.object(Bot, "__call__", new_callable=AsyncMock, return_value=True) as api:
            for index, text in enumerate(
                ("/start", "/help", "/quality", "/language", "ordinary text"), start=1
            ):
                await dispatcher.feed_update(bot, message_update(index, text))
            await dispatcher.feed_update(bot, message_update(10, "https://example.test/track"))
            async with database.transaction() as repositories:
                request = await repositories.telegram_delivery.get_by_message(
                    telegram_bot_id=100, telegram_chat_id=700, source_message_id=10
                )
            assert request is not None
            assert request.status is TelegramDeliveryStatus.AWAITING_QUALITY
            callback_message = Message(
                message_id=11,
                date=datetime.now(UTC),
                chat=chat,
                from_user=telegram_user,
                text="choose",
            )
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=11,
                    callback_query=CallbackQuery(
                        id="quality-callback",
                        from_user=telegram_user,
                        chat_instance="chat",
                        message=callback_message,
                        data=encode_first_quality(request.id, QualityProfile.MP3_320),
                    ),
                ),
            )
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=12,
                    callback_query=CallbackQuery(
                        id="language-callback",
                        from_user=telegram_user,
                        chat_instance="chat",
                        message=callback_message,
                        data="l1:ru",
                    ),
                ),
            )
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=13,
                    callback_query=CallbackQuery(
                        id="setting-quality-callback",
                        from_user=telegram_user,
                        chat_instance="chat",
                        message=callback_message,
                        data="sq1:4",
                    ),
                ),
            )
            assert api.await_count >= 7
        async with database.transaction() as repositories:
            stored_user = await repositories.users.get_by_telegram_id(700)
            stored_request = await repositories.telegram_delivery.get(request.id)
            assert stored_user is not None
            assert stored_user.preferred_quality_profile is QualityProfile.LOSSLESS
            assert stored_user.preferred_locale == "ru"
            assert stored_request is not None
            assert stored_request.status is TelegramDeliveryStatus.QUEUED
            assert stored_request.quality_profile is QualityProfile.MP3_320
    finally:
        await bot.session.close()
