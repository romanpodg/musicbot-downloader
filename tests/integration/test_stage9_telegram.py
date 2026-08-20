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
from app.services.telegram_requests import (
    TelegramTrackRequestService,
    TrackRequestActionOutcome,
)
from app.services.telegram_upload import TelegramCacheUploadExecutor
from app.services.telegram_users import TelegramUserProfile, TelegramUserService
from app.services.workers import DownloadWorkerBackend, UploadWorkerBackend
from app.storage import Database
from app.storage.models import (
    DownloadJob,
    JobSubscriber,
    TelegramDeliveryRequest,
    TelegramFileCache,
    UploadJob,
)
from app.storage.models.base import utc_now
from app.telegram import (
    TelegramCachedMediaSpec,
    TelegramDeliveryReceipt,
    TelegramGatewayError,
    TelegramUploadSpec,
)
from app.telegram.handlers import TelegramHandlerDependencies, create_stage9_router
from app.telegram.presentation import (
    TelegramPresentation,
    encode_first_quality,
    encode_other_quality,
    encode_track_download,
    encode_track_quality,
)


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
    first_wave = []
    for index in range(100):
        user = await _user(database, 1000 + index, QualityProfile.MP3_320)
        request = await requests.request_track(
            user=user,
            telegram_chat_id=1000 + index,
            source_message_id=1,
            url="https://example.test/track",
        )
        assert request.status is TelegramDeliveryStatus.AWAITING_ACTION
        first_wave.append(request)
    gateway = Gateway()
    delivery_worker = _worker(database, gateway, wake)
    assert await delivery_worker.claim("before-action") is None
    async with database.transaction() as repositories:
        session = repositories.singleflight._session  # noqa: SLF001
        assert await session.scalar(select(func.count(DownloadJob.id))) == 0
        assert await session.scalar(select(func.count(JobSubscriber.id))) == 0
        assert await session.scalar(select(func.count(UploadJob.id))) == 0
    for index, request in enumerate(first_wave):
        started = await requests.start_default_quality(
            request_id=request.id, telegram_user_id=1000 + index
        )
        assert started.accepted
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

    second_wave = []
    for index in range(100):
        user = await _user(database, 2000 + index, QualityProfile.MP3_320)
        request = await requests.request_track(
            user=user,
            telegram_chat_id=2000 + index,
            source_message_id=1,
            url="https://example.test/track",
        )
        second_wave.append(request)
    assert len(gateway.sent) == 100
    for index, request in enumerate(second_wave):
        started = await requests.start_default_quality(
            request_id=request.id, telegram_user_id=2000 + index
        )
        assert started.accepted
    await _drain(delivery_worker, 100)
    await _drain(delivery_worker, 100)
    assert gateway.sent == ["shared-file-id"] * 200
    async with database.transaction() as repositories:
        session = repositories.singleflight._session  # noqa: SLF001
        assert await session.scalar(select(func.count(DownloadJob.id))) == 1
        assert await session.scalar(select(func.count(JobSubscriber.id))) == 100
        assert await session.scalar(select(func.count(UploadJob.id))) == 1


async def test_one_off_quality_is_restart_safe_and_does_not_change_preference(
    database: Database,
) -> None:
    track_id, _ = await _track(database)
    user = await _user(database, 550, QualityProfile.MP3_320)
    initial = TelegramTrackRequestService(database, Resolver(track_id), telegram_bot_id=100)
    request = await initial.request_track(
        user=user,
        telegram_chat_id=550,
        source_message_id=1,
        url="https://example.test/track",
    )
    assert request.status is TelegramDeliveryStatus.AWAITING_ACTION
    assert request.quality_profile is QualityProfile.MP3_320

    restarted = TelegramTrackRequestService(database, Resolver(track_id), telegram_bot_id=100)
    opened = await restarted.open_track_quality(request_id=request.id, telegram_user_id=550)
    assert opened.accepted
    restarted_again = TelegramTrackRequestService(database, Resolver(track_id), telegram_bot_id=100)
    selected = await restarted_again.choose_track_quality(
        request_id=request.id,
        telegram_user_id=550,
        quality_profile=QualityProfile.LOSSLESS,
    )
    assert selected.accepted
    assert selected.request is not None
    assert selected.request.status is TelegramDeliveryStatus.QUEUED
    assert selected.request.quality_profile is QualityProfile.LOSSLESS
    async with database.transaction() as repositories:
        stored_user = await repositories.users.get_by_telegram_id(550)
        assert stored_user is not None
        assert stored_user.preferred_quality_profile is QualityProfile.MP3_320

    next_request = await restarted_again.request_track(
        user=user,
        telegram_chat_id=550,
        source_message_id=2,
        url="https://example.test/track",
    )
    assert next_request.status is TelegramDeliveryStatus.AWAITING_ACTION
    assert next_request.quality_profile is QualityProfile.MP3_320


async def test_old_card_keeps_snapshot_after_global_quality_change(database: Database) -> None:
    track_id, _ = await _track(database)
    user = await _user(database, 551, QualityProfile.MP3_320)
    service = TelegramTrackRequestService(database, Resolver(track_id), telegram_bot_id=100)
    old = await service.request_track(
        user=user,
        telegram_chat_id=551,
        source_message_id=1,
        url="https://example.test/track",
    )
    async with database.transaction() as repositories:
        stored_user = await repositories.users.get_by_telegram_id(551)
        assert stored_user is not None
        await repositories.users.set_preferred_quality(stored_user, QualityProfile.LOSSLESS)
    started = await service.start_default_quality(request_id=old.id, telegram_user_id=551)
    assert started.accepted
    assert started.request is not None
    assert started.request.quality_profile is QualityProfile.MP3_320

    new = await service.request_track(
        user=user,
        telegram_chat_id=551,
        source_message_id=2,
        url="https://example.test/track",
    )
    assert new.quality_profile is QualityProfile.LOSSLESS


async def test_track_card_action_ownership_staleness_and_message_identity(
    database: Database,
) -> None:
    track_id, _ = await _track(database)
    owner = await _user(database, 552, QualityProfile.MP3_320)
    await _user(database, 553, QualityProfile.LOSSLESS)
    service = TelegramTrackRequestService(database, Resolver(track_id), telegram_bot_id=100)
    request = await service.request_track(
        user=owner,
        telegram_chat_id=552,
        source_message_id=1,
        url="https://example.test/track",
    )
    assert await service.record_card_message(
        request_id=request.id, telegram_user_id=552, message_id=99
    )
    assert not await service.record_card_message(
        request_id=request.id, telegram_user_id=552, message_id=100
    )
    hijack = await service.start_default_quality(request_id=request.id, telegram_user_id=553)
    assert hijack.outcome is TrackRequestActionOutcome.FORBIDDEN
    started = await service.start_default_quality(request_id=request.id, telegram_user_id=552)
    assert started.accepted
    duplicate = await service.start_default_quality(request_id=request.id, telegram_user_id=552)
    alternate = await service.open_track_quality(request_id=request.id, telegram_user_id=552)
    assert duplicate.outcome is TrackRequestActionOutcome.STALE
    assert alternate.outcome is TrackRequestActionOutcome.STALE
    assert duplicate.request is not None
    assert duplicate.request.quality_profile is QualityProfile.MP3_320


async def test_primary_other_and_two_quality_races_have_one_winner(database: Database) -> None:
    track_id, _ = await _track(database)
    user = await _user(database, 554, QualityProfile.MP3_320)
    service = TelegramTrackRequestService(database, Resolver(track_id), telegram_bot_id=100)
    request = await service.request_track(
        user=user,
        telegram_chat_id=554,
        source_message_id=1,
        url="https://example.test/track",
    )
    primary, other = await asyncio.gather(
        service.start_default_quality(request_id=request.id, telegram_user_id=554),
        service.open_track_quality(request_id=request.id, telegram_user_id=554),
    )
    assert sum(result.accepted for result in (primary, other)) == 1
    async with database.transaction() as repositories:
        current = await repositories.telegram_delivery.get(request.id)
        assert current is not None
        assert current.status in {
            TelegramDeliveryStatus.QUEUED,
            TelegramDeliveryStatus.AWAITING_TRACK_QUALITY,
        }

    request2 = await service.request_track(
        user=user,
        telegram_chat_id=554,
        source_message_id=2,
        url="https://example.test/track",
    )
    assert (await service.open_track_quality(request_id=request2.id, telegram_user_id=554)).accepted
    aac, lossless = await asyncio.gather(
        service.choose_track_quality(
            request_id=request2.id,
            telegram_user_id=554,
            quality_profile=QualityProfile.AAC_256,
        ),
        service.choose_track_quality(
            request_id=request2.id,
            telegram_user_id=554,
            quality_profile=QualityProfile.LOSSLESS,
        ),
    )
    assert sum(result.accepted for result in (aac, lossless)) == 1
    async with database.transaction() as repositories:
        final = await repositories.telegram_delivery.get(request2.id)
        assert final is not None
        assert final.status is TelegramDeliveryStatus.QUEUED
        assert final.quality_profile in {QualityProfile.AAC_256, QualityProfile.LOSSLESS}


async def test_mixed_quality_fanout_uses_two_flights_and_isolated_deliveries(
    database: Database, tmp_path: Path
) -> None:
    track_id, source_id = await _track(database)
    service = TelegramTrackRequestService(database, Resolver(track_id), telegram_bot_id=100)
    requests = []
    for index in range(100):
        telegram_id = 3000 + index
        user = await _user(database, telegram_id, QualityProfile.MP3_320)
        request = await service.request_track(
            user=user,
            telegram_chat_id=telegram_id,
            source_message_id=1,
            url="https://example.test/track",
        )
        requests.append(request)
        if index < 60:
            assert (
                await service.start_default_quality(
                    request_id=request.id, telegram_user_id=telegram_id
                )
            ).accepted
        else:
            assert (
                await service.open_track_quality(
                    request_id=request.id, telegram_user_id=telegram_id
                )
            ).accepted
            assert (
                await service.choose_track_quality(
                    request_id=request.id,
                    telegram_user_id=telegram_id,
                    quality_profile=QualityProfile.LOSSLESS,
                )
            ).accepted

    gateway = Gateway()
    worker = _worker(database, gateway, asyncio.Event())
    await _drain(worker, 100)
    async with database.transaction() as repositories:
        session = repositories.singleflight._session  # noqa: SLF001
        assert await session.scalar(select(func.count(DownloadJob.id))) == 2
        assert await session.scalar(select(func.count(JobSubscriber.id))) == 100
        grouped = dict(
            (
                await session.execute(
                    select(
                        TelegramDeliveryRequest.quality_profile,
                        func.count(TelegramDeliveryRequest.id),
                    ).group_by(TelegramDeliveryRequest.quality_profile)
                )
            ).all()
        )
        assert grouped == {QualityProfile.MP3_320: 60, QualityProfile.LOSSLESS: 40}

    artifacts = DownloadArtifactManager(tmp_path / "mixed-artifacts")
    notifier = SubscriberNotifier()
    uploads = UploadQueueService(database, artifacts, subscriber_notifier=notifier)
    pipeline = Pipeline(artifacts, source_id)
    downloader = DownloadWorkerBackend(database, pipeline, artifacts, subscriber_notifier=notifier)
    for number in range(2):
        job = await downloader.claim(f"mixed-download-{number}")
        assert job is not None
        await downloader.process(job, f"mixed-download-{number}")
    uploader = UploadWorkerBackend(
        database,
        TelegramCacheUploadExecutor(
            database, TelegramFileCacheService(database), gateway, cache_chat_id=-100123
        ),
        uploads,
        subscriber_notifier=notifier,
    )
    for number in range(2):
        job = await uploader.claim(f"mixed-upload-{number}")
        assert job is not None
        await uploader.process(job, f"mixed-upload-{number}")
    await _drain(worker, 100)
    assert pipeline.calls == 2
    assert gateway.uploads == 2
    assert len(gateway.sent) == 100
    async with database.transaction() as repositories:
        session = repositories.singleflight._session  # noqa: SLF001
        assert await session.scalar(select(func.count(UploadJob.id))) == 2
        assert await session.scalar(select(func.count(TelegramFileCache.id))) == 2


async def test_retry_and_invalid_file_repair_are_persistent(
    database: Database, tmp_path: Path
) -> None:
    track_id, source_id = await _track(database)
    cache = TelegramFileCacheService(database)
    gateway = Gateway()
    artifacts = DownloadArtifactManager(tmp_path / "stage9-test-artifacts")
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
    request = await service.request_track(
        user=user,
        telegram_chat_id=600,
        source_message_id=1,
        url="https://example.test/track",
    )
    assert (
        await service.start_default_quality(request_id=request.id, telegram_user_id=600)
    ).accepted
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
    request2 = await service.request_track(
        user=user2,
        telegram_chat_id=601,
        source_message_id=1,
        url="https://example.test/track",
    )
    assert (
        await service.start_default_quality(request_id=request2.id, telegram_user_id=601)
    ).accepted
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
            await dispatcher.feed_update(bot, message_update(20, "https://example.test/track"))
            async with database.transaction() as repositories:
                one_off_request = await repositories.telegram_delivery.get_by_message(
                    telegram_bot_id=100, telegram_chat_id=700, source_message_id=20
                )
            assert one_off_request is not None
            assert one_off_request.status is TelegramDeliveryStatus.AWAITING_ACTION
            assert one_off_request.quality_profile is QualityProfile.LOSSLESS
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=21,
                    callback_query=CallbackQuery(
                        id="other-quality-callback",
                        from_user=telegram_user,
                        chat_instance="chat",
                        message=callback_message,
                        data=encode_other_quality(one_off_request.id),
                    ),
                ),
            )
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=22,
                    callback_query=CallbackQuery(
                        id="track-quality-callback",
                        from_user=telegram_user,
                        chat_instance="chat",
                        message=callback_message,
                        data=encode_track_quality(one_off_request.id, QualityProfile.AAC_256),
                    ),
                ),
            )
            await dispatcher.feed_update(bot, message_update(30, "https://example.test/track"))
            async with database.transaction() as repositories:
                primary_request = await repositories.telegram_delivery.get_by_message(
                    telegram_bot_id=100, telegram_chat_id=700, source_message_id=30
                )
            assert primary_request is not None
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=31,
                    callback_query=CallbackQuery(
                        id="primary-callback",
                        from_user=telegram_user,
                        chat_instance="chat",
                        message=callback_message,
                        data=encode_track_download(primary_request.id),
                    ),
                ),
            )
            assert api.await_count >= 13
        async with database.transaction() as repositories:
            stored_user = await repositories.users.get_by_telegram_id(700)
            stored_request = await repositories.telegram_delivery.get(request.id)
            stored_one_off = await repositories.telegram_delivery.get(one_off_request.id)
            stored_primary = await repositories.telegram_delivery.get(primary_request.id)
            assert stored_user is not None
            assert stored_user.preferred_quality_profile is QualityProfile.LOSSLESS
            assert stored_user.preferred_locale == "ru"
            assert stored_request is not None
            assert stored_request.status is TelegramDeliveryStatus.QUEUED
            assert stored_request.quality_profile is QualityProfile.MP3_320
            assert stored_one_off is not None
            assert stored_one_off.status is TelegramDeliveryStatus.QUEUED
            assert stored_one_off.quality_profile is QualityProfile.AAC_256
            assert stored_primary is not None
            assert stored_primary.status is TelegramDeliveryStatus.QUEUED
            assert stored_primary.quality_profile is QualityProfile.LOSSLESS
    finally:
        await bot.session.close()
