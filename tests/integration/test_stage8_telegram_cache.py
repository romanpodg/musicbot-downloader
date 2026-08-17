from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select, update

from app.core.enums import (
    DeliveryPreparationStatus,
    DownloadPlanOperation,
    DownloadPlanReadiness,
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    QualityProfile,
    QueueErrorCode,
    QueueJobStatus,
    SubscriberStatus,
    TelegramCacheStatus,
    TelegramMediaKind,
)
from app.core.exceptions import (
    DeliveryInvariantError,
    UploadRetryableError,
    UploadTerminalError,
)
from app.core.models import (
    DownloadArtifactMetadata,
    DownloadResult,
    PreparedSourceMedia,
    TelegramBotIdentity,
    TelegramUploadReceipt,
    UploadRequest,
)
from app.services.artifacts import DownloadArtifactManager
from app.services.delivery import DeliveryPreparationService
from app.services.queues import UploadQueueService
from app.services.singleflight import SingleFlightService, SubscriberNotifier
from app.services.telegram_cache import TelegramFileCacheService
from app.services.telegram_upload import TelegramCacheUploadExecutor
from app.services.workers import DownloadWorkerBackend, UploadWorkerBackend
from app.storage import Database
from app.storage.models import (
    DownloadJob,
    JobSubscriber,
    TelegramFileCache,
    TrackSource,
    UploadJob,
)
from app.telegram import TelegramGatewayError, TelegramUploadSpec


class FakeTelegramGateway:
    def __init__(self, bot_id: int = 100) -> None:
        self.identity = TelegramBotIdentity(bot_id, "cache_bot")
        self.audio_calls: list[TelegramUploadSpec] = []
        self.document_calls: list[TelegramUploadSpec] = []
        self.error: TelegramGatewayError | None = None
        self._message_id = 10

    async def get_bot_identity(self) -> TelegramBotIdentity:
        if self.error is not None:
            raise self.error
        return self.identity

    async def upload_audio(self, spec: TelegramUploadSpec) -> TelegramUploadReceipt:
        if self.error is not None:
            raise self.error
        self.audio_calls.append(spec)
        return self._receipt(TelegramMediaKind.AUDIO, spec.file_path.stat().st_size)

    async def upload_document(self, spec: TelegramUploadSpec) -> TelegramUploadReceipt:
        if self.error is not None:
            raise self.error
        self.document_calls.append(spec)
        return self._receipt(TelegramMediaKind.DOCUMENT, spec.file_path.stat().st_size)

    async def close(self) -> None:
        return None

    def _receipt(self, kind: TelegramMediaKind, size: int) -> TelegramUploadReceipt:
        self._message_id += 1
        return TelegramUploadReceipt(
            self.identity.telegram_bot_id,
            -100123,
            self._message_id,
            kind,
            f"file-{self._message_id}",
            f"unique-{self._message_id}",
            size,
        )

    @property
    def upload_calls(self) -> int:
        return len(self.audio_calls) + len(self.document_calls)


@dataclass
class Pipeline:
    artifacts: DownloadArtifactManager
    source_id: int
    calls: int = 0

    async def download(self, track_id: int, quality_profile: QualityProfile) -> DownloadResult:
        self.calls += 1
        job_id, _ = self.artifacts.create_job()
        path = self.artifacts.final_path(job_id, "mp3")
        path.write_bytes(b"stage-8-audio")
        source = PreparedSourceMedia(
            MusicProviderName.QOBUZ,
            "qobuz-source",
            codec=NativeCodec.FLAC,
            container=NativeContainer.FLAC,
            bitrate_kbps=1000,
            sample_rate_hz=96000,
            bit_depth=24,
            channels=2,
            duration_ms=123000,
            lossless=True,
        )
        output = PreparedSourceMedia(
            MusicProviderName.QOBUZ,
            "qobuz-source",
            codec=NativeCodec.MP3,
            container=NativeContainer.MP3,
            bitrate_kbps=320,
            sample_rate_hz=48000,
            channels=2,
            duration_ms=123000,
            lossless=False,
            file_path=path,
        )
        return DownloadResult(
            job_id=job_id,
            track_id=track_id,
            requested_profile=quality_profile,
            track_source_id=self.source_id,
            provider=MusicProviderName.QOBUZ,
            provider_track_id="qobuz-source",
            operation=DownloadPlanOperation.TRANSCODE,
            plan_readiness=DownloadPlanReadiness.CONFIRMED,
            source_media=source,
            output_media=output,
            file_path=path,
            file_size=path.stat().st_size,
            transcoded=True,
            fallback_index=1,
            attempts=(),
            created_at=datetime.now(UTC),
            encoder="libmp3lame",
        )


async def _track_and_source(database: Database, suffix: str = "1") -> tuple[int, int]:
    async with database.transaction() as repositories:
        track = await repositories.tracks.create_track(
            title=f"Title {suffix}", artist="Artist", duration_ms=123000
        )
        source = await repositories.track_sources.upsert_source(
            track_id=track.id,
            provider=MusicProviderName.QOBUZ,
            provider_track_id=f"qobuz-{suffix}",
            url=f"https://example.test/{suffix}",
        )
        return track.id, source.source.id


def _metadata(
    source_id: int | None,
    *,
    provider: MusicProviderName = MusicProviderName.QOBUZ,
    container: NativeContainer = NativeContainer.MP3,
    codec: NativeCodec = NativeCodec.MP3,
    size: int = 5,
) -> DownloadArtifactMetadata:
    return DownloadArtifactMetadata(
        track_source_id=source_id,
        source_provider=provider,
        source_provider_track_id="actual-fallback-source",
        operation=DownloadPlanOperation.TRANSCODE,
        transcoded=True,
        source_codec=NativeCodec.FLAC,
        source_container=NativeContainer.FLAC,
        source_bitrate_kbps=1000,
        output_codec=codec,
        output_container=container,
        output_bitrate_kbps=320 if codec is NativeCodec.MP3 else 256,
        sample_rate_hz=48000,
        bit_depth=24 if codec is NativeCodec.FLAC else None,
        channels=2,
        duration_ms=123000,
        file_size_bytes=size,
        encoder="libmp3lame" if codec is NativeCodec.MP3 else "aac",
    )


def _receipt(
    *, bot_id: int = 100, kind: TelegramMediaKind = TelegramMediaKind.AUDIO, size: int = 5
) -> TelegramUploadReceipt:
    return TelegramUploadReceipt(bot_id, -100123, 77, kind, "file-id", "file-unique-id", size)


async def _cache(
    cache: TelegramFileCacheService,
    track_id: int,
    source_id: int | None,
    *,
    bot_id: int = 100,
    quality: QualityProfile = QualityProfile.MP3_320,
) -> None:
    await cache.upsert_success(
        track_id=track_id,
        quality_profile=quality,
        receipt=_receipt(bot_id=bot_id),
        artifact=_metadata(source_id),
    )


async def test_cache_persists_exact_key_status_and_provenance(database: Database) -> None:
    track_id, source_id = await _track_and_source(database)
    cache = TelegramFileCacheService(database)
    await _cache(cache, track_id, source_id)
    recreated = TelegramFileCacheService(database)

    hit = await recreated.get_active(
        telegram_bot_id=100,
        track_id=track_id,
        quality_profile=QualityProfile.MP3_320,
    )
    assert hit is not None
    assert hit.file_id == "file-id"
    assert hit.file_unique_id == "file-unique-id"
    assert hit.cache_chat_id == -100123 and hit.cache_message_id == 77
    assert hit.source_track_source_id == source_id
    assert hit.source_provider is MusicProviderName.QOBUZ
    assert hit.source_provider_track_id == "actual-fallback-source"
    assert hit.output_codec is NativeCodec.MP3 and hit.output_bitrate_kbps == 320
    assert hit.encoder == "libmp3lame"
    assert hit.status is TelegramCacheStatus.ACTIVE
    assert hit.last_used_at is None

    assert (
        await recreated.get_active(
            telegram_bot_id=200,
            track_id=track_id,
            quality_profile=QualityProfile.MP3_320,
        )
        is None
    )
    assert (
        await recreated.get_active(
            telegram_bot_id=100,
            track_id=track_id,
            quality_profile=QualityProfile.AAC_256,
        )
        is None
    )


async def test_concurrent_upsert_converges_and_invalidation_reactivates(
    database: Database,
) -> None:
    track_id, source_id = await _track_and_source(database)
    cache = TelegramFileCacheService(database)
    await asyncio.gather(*(_cache(cache, track_id, source_id) for _ in range(20)))
    async with database.transaction() as repositories:
        count = await repositories.telegram_cache._session.scalar(  # noqa: SLF001
            select(func.count(TelegramFileCache.id))
        )
    assert count == 1
    entry = (await cache.list_entries())[0]
    invalid = await cache.invalidate(entry.cache_id, reason_code="FILE_REFERENCE_INVALID")
    assert invalid.status is TelegramCacheStatus.INVALID
    assert invalid.invalidated_at is not None
    assert (
        await cache.get_active(
            telegram_bot_id=100,
            track_id=track_id,
            quality_profile=QualityProfile.MP3_320,
        )
        is None
    )

    replacement = await cache.upsert_success(
        track_id=track_id,
        quality_profile=QualityProfile.MP3_320,
        receipt=TelegramUploadReceipt(
            100, -100999, 88, TelegramMediaKind.AUDIO, "replacement", "unique-2", 5
        ),
        artifact=_metadata(source_id),
    )
    assert replacement.cache_id == entry.cache_id
    assert replacement.status is TelegramCacheStatus.ACTIVE
    assert replacement.file_id == "replacement"
    assert replacement.invalidated_at is None


async def test_invalid_cache_storm_creates_one_fresh_flight(database: Database) -> None:
    track_id, source_id = await _track_and_source(database)
    cache = TelegramFileCacheService(database)
    await _cache(cache, track_id, source_id)
    entry = (await cache.list_entries())[0]
    await cache.invalidate(entry.cache_id, reason_code="STALE_REFERENCE")
    delivery = DeliveryPreparationService(database, telegram_bot_id=100, max_size=10)

    results = await asyncio.gather(
        *(
            delivery.prepare(
                track_id=track_id,
                quality_profile=QualityProfile.MP3_320,
                request_key=f"invalid-{index}",
            )
            for index in range(100)
        )
    )
    assert all(result.status is DeliveryPreparationStatus.PENDING for result in results)
    assert len({result.download_job_id for result in results}) == 1
    async with database.transaction() as repositories:
        jobs = await repositories.singleflight._session.scalar(  # noqa: SLF001
            select(func.count(DownloadJob.id))
        )
        subscribers = await repositories.singleflight._session.scalar(  # noqa: SLF001
            select(func.count(JobSubscriber.id))
        )
    assert jobs == 1 and subscribers == 100


async def test_cache_commit_and_prepare_race_never_creates_post_cache_job(
    database: Database,
) -> None:
    track_id, source_id = await _track_and_source(database)
    cache = TelegramFileCacheService(database)
    delivery = DeliveryPreparationService(database, telegram_bot_id=100, max_size=10)
    original = await delivery.prepare(
        track_id=track_id,
        quality_profile=QualityProfile.MP3_320,
        request_key="original",
    )
    assert original.download_job_id is not None

    await asyncio.gather(
        _cache(cache, track_id, source_id),
        *(
            delivery.prepare(
                track_id=track_id,
                quality_profile=QualityProfile.MP3_320,
                request_key=f"race-{index}",
            )
            for index in range(50)
        ),
    )
    after_commit = await asyncio.gather(
        *(
            delivery.prepare(
                track_id=track_id,
                quality_profile=QualityProfile.MP3_320,
                request_key=f"after-{index}",
            )
            for index in range(20)
        )
    )
    assert all(result.status is DeliveryPreparationStatus.CACHE_HIT for result in after_commit)
    async with database.transaction() as repositories:
        jobs = await repositories.singleflight._session.scalar(  # noqa: SLF001
            select(func.count(DownloadJob.id))
        )
    assert jobs == 1


async def test_cache_provenance_survives_track_source_deletion(database: Database) -> None:
    track_id, source_id = await _track_and_source(database)
    cache = TelegramFileCacheService(database)
    await _cache(cache, track_id, source_id)
    async with database.engine.begin() as connection:
        await connection.execute(delete(TrackSource).where(TrackSource.id == source_id))

    hit = await cache.get_active(
        telegram_bot_id=100,
        track_id=track_id,
        quality_profile=QualityProfile.MP3_320,
    )
    assert hit is not None
    assert hit.source_track_source_id is None
    assert hit.source_provider is MusicProviderName.QOBUZ
    assert hit.source_provider_track_id == "actual-fallback-source"
    assert hit.last_used_at is None
    assert await cache.mark_used(hit.cache_id)
    assert (await cache.get(hit.cache_id)).last_used_at is not None


async def test_cache_hit_and_miss_storms_preserve_singleflight(database: Database) -> None:
    track_id, source_id = await _track_and_source(database)
    cache = TelegramFileCacheService(database)
    await _cache(cache, track_id, source_id)
    service = DeliveryPreparationService(database, telegram_bot_id=100, max_size=10)

    hits = await asyncio.gather(
        *(
            service.prepare(track_id=track_id, quality_profile=QualityProfile.MP3_320)
            for _ in range(100)
        )
    )
    assert all(result.status is DeliveryPreparationStatus.CACHE_HIT for result in hits)
    async with database.transaction() as repositories:
        jobs = await repositories.singleflight._session.scalar(  # noqa: SLF001
            select(func.count(DownloadJob.id))
        )
        subscribers = await repositories.singleflight._session.scalar(  # noqa: SLF001
            select(func.count(JobSubscriber.id))
        )
    assert jobs == 0 and subscribers == 0

    misses = await asyncio.gather(
        *(
            service.prepare(
                track_id=track_id,
                quality_profile=QualityProfile.AAC_256,
                request_key=f"miss-{index}",
            )
            for index in range(100)
        )
    )
    assert all(result.status is DeliveryPreparationStatus.PENDING for result in misses)
    assert len({result.download_job_id for result in misses}) == 1
    async with database.transaction() as repositories:
        jobs = await repositories.singleflight._session.scalar(  # noqa: SLF001
            select(func.count(DownloadJob.id))
        )
        subscribers = await repositories.singleflight._session.scalar(  # noqa: SLF001
            select(func.count(JobSubscriber.id))
        )
    assert jobs == 1 and subscribers == 100


async def test_telegram_upload_executor_is_idempotent_and_uses_media_policy(
    database: Database, tmp_path: Path
) -> None:
    track_id, source_id = await _track_and_source(database)
    cache = TelegramFileCacheService(database)
    gateway = FakeTelegramGateway()
    executor = TelegramCacheUploadExecutor(database, cache, gateway, cache_chat_id=-100123)
    path = tmp_path / "final.mp3"
    path.write_bytes(b"audio")
    request = UploadRequest(
        1,
        1,
        track_id,
        QualityProfile.MP3_320,
        "artifact",
        path,
        _metadata(source_id),
    )

    first = await executor.upload(request)
    second = await executor.upload(request)
    assert first.external_id == second.external_id
    assert gateway.upload_calls == 1
    assert len(gateway.audio_calls) == 1
    assert gateway.audio_calls[0].display_filename == "Artist - Title 1.mp3"

    lossless_path = tmp_path / "final.flac"
    lossless_path.write_bytes(b"flac!")
    lossless_track, lossless_source = await _track_and_source(database, "2")
    lossless = UploadRequest(
        2,
        2,
        lossless_track,
        QualityProfile.LOSSLESS,
        "artifact2",
        lossless_path,
        _metadata(
            lossless_source,
            container=NativeContainer.FLAC,
            codec=NativeCodec.FLAC,
            size=5,
        ),
    )
    await executor.upload(lossless)
    assert len(gateway.document_calls) == 1
    cached = await cache.get_active(
        telegram_bot_id=100,
        track_id=lossless_track,
        quality_profile=QualityProfile.LOSSLESS,
    )
    assert cached is not None and cached.media_kind is TelegramMediaKind.DOCUMENT
    assert cached.output_codec is NativeCodec.FLAC


async def test_upload_retry_after_cache_commit_avoids_second_network_call(
    database: Database, tmp_path: Path
) -> None:
    track_id, source_id = await _track_and_source(database)
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    notifier = SubscriberNotifier()
    uploads = UploadQueueService(database, artifacts, subscriber_notifier=notifier)
    delivery = DeliveryPreparationService(
        database, telegram_bot_id=100, max_size=10, notifier=notifier
    )
    submitted = await delivery.prepare(track_id=track_id, quality_profile=QualityProfile.MP3_320)
    pipeline = Pipeline(artifacts, source_id)
    downloader = DownloadWorkerBackend(database, pipeline, artifacts, subscriber_notifier=notifier)
    download = await downloader.claim("download-1")
    assert download is not None
    await downloader.process(download, "download-1")

    gateway = FakeTelegramGateway()
    cache = TelegramFileCacheService(database)
    executor = TelegramCacheUploadExecutor(database, cache, gateway, cache_chat_id=-100123)
    backend = UploadWorkerBackend(database, executor, uploads, subscriber_notifier=notifier)
    upload = await backend.claim("upload-1")
    assert upload is not None
    assert upload.source_provider is not None
    assert upload.source_provider_track_id is not None
    assert upload.operation is not None
    assert upload.transcoded is not None
    assert upload.file_size_bytes is not None
    path = uploads.validate_artifact(upload.artifact_job_id, upload.artifact_path)
    request = UploadRequest(
        upload.id,
        upload.download_job_id,
        upload.track_id,
        upload.quality_profile,
        upload.artifact_job_id,
        path,
        DownloadArtifactMetadata(
            upload.source_track_source_id,
            upload.source_provider,
            upload.source_provider_track_id,
            upload.operation,
            upload.transcoded,
            upload.source_codec,
            upload.source_container,
            upload.source_bitrate_kbps,
            upload.output_codec,
            upload.output_container,
            upload.output_bitrate_kbps,
            upload.sample_rate_hz,
            upload.bit_depth,
            upload.channels,
            upload.duration_ms,
            upload.file_size_bytes,
            upload.encoder,
        ),
    )
    await executor.upload(request)
    assert gateway.upload_calls == 1

    await backend.process(upload, "upload-1")
    assert gateway.upload_calls == 1
    assert submitted.subscriber is not None
    ready = await delivery.get_ready_file(submitted.subscriber.id)
    assert ready.track_id == track_id
    assert not path.exists()


async def test_completed_result_reuse_end_to_end(database: Database, tmp_path: Path) -> None:
    track_id, source_id = await _track_and_source(database)
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    notifier = SubscriberNotifier()
    uploads = UploadQueueService(database, artifacts, subscriber_notifier=notifier)
    delivery = DeliveryPreparationService(
        database, telegram_bot_id=100, max_size=10, notifier=notifier
    )
    first_wave = await asyncio.gather(
        *(
            delivery.prepare(
                track_id=track_id,
                quality_profile=QualityProfile.MP3_320,
                request_key=f"first-{index}",
            )
            for index in range(100)
        )
    )
    assert len({result.download_job_id for result in first_wave}) == 1

    pipeline = Pipeline(artifacts, source_id)
    download_backend = DownloadWorkerBackend(
        database, pipeline, artifacts, subscriber_notifier=notifier
    )
    download = await download_backend.claim("download-1")
    assert download is not None
    await download_backend.process(download, "download-1")
    cache = TelegramFileCacheService(database)
    gateway = FakeTelegramGateway()
    upload_backend = UploadWorkerBackend(
        database,
        TelegramCacheUploadExecutor(database, cache, gateway, cache_chat_id=-100123),
        uploads,
        subscriber_notifier=notifier,
    )
    upload = await upload_backend.claim("upload-1")
    assert upload is not None
    artifact_path = uploads.validate_artifact(upload.artifact_job_id, upload.artifact_path)
    await upload_backend.process(upload, "upload-1")
    assert pipeline.calls == 1 and gateway.upload_calls == 1
    assert not artifact_path.exists()
    first_subscriber = first_wave[0].subscriber
    assert first_subscriber is not None
    assert (await delivery.get_ready_file(first_subscriber.id)).file_id

    second_wave = await asyncio.gather(
        *(
            delivery.prepare(
                track_id=track_id,
                quality_profile=QualityProfile.MP3_320,
                request_key=f"second-{index}",
            )
            for index in range(100)
        )
    )
    assert all(result.status is DeliveryPreparationStatus.CACHE_HIT for result in second_wave)
    assert gateway.upload_calls == 1
    async with database.transaction() as repositories:
        jobs = await repositories.singleflight._session.scalar(  # noqa: SLF001
            select(func.count(DownloadJob.id))
        )
        subscribers = await repositories.singleflight._session.scalar(  # noqa: SLF001
            select(func.count(JobSubscriber.id))
        )
        upload_jobs = await repositories.singleflight._session.scalar(  # noqa: SLF001
            select(func.count(UploadJob.id))
        )
    assert jobs == 1 and subscribers == 100 and upload_jobs == 1


async def test_ready_without_cache_is_invariant_error(database: Database) -> None:
    track_id, _ = await _track_and_source(database)
    delivery = DeliveryPreparationService(database, telegram_bot_id=100, max_size=10)
    pending = await delivery.prepare(track_id=track_id, quality_profile=QualityProfile.MP3_128)
    assert pending.subscriber is not None
    async with database.engine.begin() as connection:
        await connection.execute(
            update(JobSubscriber)
            .where(JobSubscriber.id == pending.subscriber.id)
            .values(status=SubscriberStatus.READY, completed_at=datetime.now(UTC))
        )
    with pytest.raises(DeliveryInvariantError):
        await delivery.get_ready_file(pending.subscriber.id)


async def test_gateway_rate_limit_and_permission_map_to_worker_contract(
    database: Database, tmp_path: Path
) -> None:
    track_id, source_id = await _track_and_source(database)
    path = tmp_path / "final.mp3"
    path.write_bytes(b"audio")
    cache = TelegramFileCacheService(database)
    gateway = FakeTelegramGateway()
    executor = TelegramCacheUploadExecutor(database, cache, gateway, cache_chat_id=-100123)
    request = UploadRequest(
        1,
        1,
        track_id,
        QualityProfile.MP3_320,
        "artifact",
        path,
        _metadata(source_id),
    )
    gateway.error = TelegramGatewayError(
        QueueErrorCode.TELEGRAM_RATE_LIMITED.value,
        retryable=True,
        retry_after_seconds=17,
    )
    with pytest.raises(UploadRetryableError) as retry:
        await executor.upload(request)
    assert retry.value.retry_after_seconds == 17
    assert retry.value.code is QueueErrorCode.TELEGRAM_RATE_LIMITED

    gateway.error = TelegramGatewayError(
        QueueErrorCode.TELEGRAM_PERMISSION_DENIED.value, retryable=False
    )
    with pytest.raises(UploadTerminalError) as terminal:
        await executor.upload(request)
    assert terminal.value.code is QueueErrorCode.TELEGRAM_PERMISSION_DENIED


@pytest.mark.parametrize(
    ("gateway_error", "expected_status", "expected_subscriber", "artifact_remains"),
    [
        (
            TelegramGatewayError(
                QueueErrorCode.TELEGRAM_RATE_LIMITED.value,
                retryable=True,
                retry_after_seconds=17,
            ),
            QueueJobStatus.QUEUED,
            SubscriberStatus.WAITING,
            True,
        ),
        (
            TelegramGatewayError(QueueErrorCode.TELEGRAM_PERMISSION_DENIED.value, retryable=False),
            QueueJobStatus.FAILED,
            SubscriberStatus.FAILED,
            False,
        ),
    ],
)
async def test_worker_persists_telegram_retry_or_terminal_outcome(
    database: Database,
    tmp_path: Path,
    gateway_error: TelegramGatewayError,
    expected_status: QueueJobStatus,
    expected_subscriber: SubscriberStatus,
    artifact_remains: bool,
) -> None:
    track_id, source_id = await _track_and_source(database)
    now = datetime(2026, 8, 18, tzinfo=UTC)
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    notifier = SubscriberNotifier()
    uploads = UploadQueueService(
        database, artifacts, clock=lambda: now, subscriber_notifier=notifier
    )
    delivery = DeliveryPreparationService(
        database,
        telegram_bot_id=100,
        max_size=10,
        clock=lambda: now,
        notifier=notifier,
    )
    pending = await delivery.prepare(track_id=track_id, quality_profile=QualityProfile.MP3_320)
    assert pending.subscriber is not None
    downloader = DownloadWorkerBackend(
        database,
        Pipeline(artifacts, source_id),
        artifacts,
        clock=lambda: now,
        subscriber_notifier=notifier,
    )
    download = await downloader.claim("download-1")
    assert download is not None
    await downloader.process(download, "download-1")

    gateway = FakeTelegramGateway()
    gateway.error = gateway_error
    cache = TelegramFileCacheService(database, clock=lambda: now)
    backend = UploadWorkerBackend(
        database,
        TelegramCacheUploadExecutor(database, cache, gateway, cache_chat_id=-100123),
        uploads,
        clock=lambda: now,
        subscriber_notifier=notifier,
    )
    upload = await backend.claim("upload-1")
    assert upload is not None
    artifact = uploads.validate_artifact(upload.artifact_job_id, upload.artifact_path)
    await backend.process(upload, "upload-1")

    stored = await uploads.get_upload_job(upload.id)
    assert stored.status is expected_status
    assert (
        await SingleFlightService(database, max_size=10).get_subscriber(pending.subscriber.id)
    ).status is expected_subscriber
    assert artifact.exists() is artifact_remains
    assert (
        await cache.get_active(
            telegram_bot_id=100,
            track_id=track_id,
            quality_profile=QualityProfile.MP3_320,
        )
        is None
    )
    if expected_status is QueueJobStatus.QUEUED:
        assert stored.last_error_code == QueueErrorCode.TELEGRAM_RATE_LIMITED.value
        assert stored.available_at >= now + timedelta(seconds=17)
