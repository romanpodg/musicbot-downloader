from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from app.application.download import DownloadService, DownloadTrackUseCase
from app.core.delivery_targets import PrivateUserTarget
from app.core.download import DownloadDeliveryTarget, DownloadSubmissionState
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
from app.core.recognition import RecognitionDecision, RecognitionResult, TrackCandidate
from app.core.search import Artist, Track
from app.core.telegram_context import TelegramChatType, TelegramContext
from app.i18n import LocalizationService
from app.services.artifacts import DownloadArtifactManager
from app.services.delivery import DeliveryPreparationService
from app.services.download_requests import ExistingDeliverySubmissionService
from app.services.queues import UploadQueueService
from app.services.singleflight import SubscriberNotifier
from app.services.telegram_cache import TelegramFileCacheService
from app.services.telegram_delivery import TelegramDeliveryWorker
from app.services.telegram_requests import TelegramTrackRequestService
from app.services.telegram_upload import TelegramCacheUploadExecutor
from app.services.workers import DownloadWorkerBackend, UploadWorkerBackend
from app.storage import Database
from app.telegram import TelegramCachedMediaSpec, TelegramDeliveryReceipt, TelegramUploadSpec


@dataclass
class _CanonicalResolver:
    track_id: int
    calls: list[Track]

    async def resolve_track_id(self, track: Track) -> int:
        self.calls.append(track)
        return self.track_id


@dataclass
class _UnusedTrackResolver:
    async def resolve_track_id(self, url: str) -> int:
        raise AssertionError(
            f"Stage 18 must use the recognized-track resolver, not URL resolution: {url}"
        )


@dataclass
class _Stage25ResolverSpy:
    calls: int = 0

    async def resolve(self, identity, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        return ()


@dataclass
class _Stage25RankerSpy:
    calls: int = 0

    def rank(self, candidates, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        return tuple(candidates)


class _Gateway:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.message_id = 10

    async def get_bot_identity(self) -> TelegramBotIdentity:
        return TelegramBotIdentity(100, "stage18_bot")

    async def upload_audio(self, spec: TelegramUploadSpec) -> TelegramUploadReceipt:
        self.message_id += 1
        return TelegramUploadReceipt(
            100,
            -100123,
            self.message_id,
            TelegramMediaKind.AUDIO,
            "stage18-file-id",
            "stage18-unique-id",
            spec.file_path.stat().st_size,
        )

    async def upload_document(self, spec: TelegramUploadSpec) -> TelegramUploadReceipt:
        return await self.upload_audio(spec)

    async def send_cached_audio(self, spec: TelegramCachedMediaSpec) -> TelegramDeliveryReceipt:
        self.sent.append(spec.file_id)
        self.message_id += 1
        return TelegramDeliveryReceipt(spec.chat_id, self.message_id)

    async def send_cached_document(self, spec: TelegramCachedMediaSpec) -> TelegramDeliveryReceipt:
        return await self.send_cached_audio(spec)

    async def send_text(self, chat_id: int, text: str) -> TelegramDeliveryReceipt:
        self.message_id += 1
        return TelegramDeliveryReceipt(chat_id, self.message_id)


@dataclass
class _Pipeline:
    artifacts: DownloadArtifactManager
    source_id: int
    calls: int = 0

    async def download(self, track_id: int, quality_profile: QualityProfile) -> DownloadResult:
        self.calls += 1
        artifact_job_id, _ = self.artifacts.create_job()
        path = self.artifacts.final_path(artifact_job_id, "mp3")
        path.write_bytes(b"stage18-audio")
        source = PreparedSourceMedia(
            MusicProviderName.QOBUZ,
            "qobuz-stage18",
            codec=NativeCodec.FLAC,
            container=NativeContainer.FLAC,
            bitrate_kbps=1000,
            lossless=True,
        )
        output = PreparedSourceMedia(
            MusicProviderName.QOBUZ,
            "qobuz-stage18",
            codec=NativeCodec.MP3,
            container=NativeContainer.MP3,
            bitrate_kbps=320,
            lossless=False,
            file_path=path,
        )
        return DownloadResult(
            artifact_job_id,
            track_id,
            quality_profile,
            self.source_id,
            MusicProviderName.QOBUZ,
            "qobuz-stage18",
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


async def _canonical_track(database: Database) -> tuple[int, int]:
    async with database.transaction() as repositories:
        track = await repositories.tracks.create_track(title="One More Time", artist="Daft Punk")
        source = await repositories.track_sources.upsert_source(
            track_id=track.id,
            provider=MusicProviderName.QOBUZ,
            provider_track_id="qobuz-stage18",
            url="https://example.test/qobuz-stage18",
        )
        return track.id, source.source.id


async def test_stage18_confirmed_recognition_reuses_queue_workers_and_delivery(
    database: Database, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    track_id, source_id = await _canonical_track(database)
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(
            18001, preferred_quality_profile=QualityProfile.MP3_320
        )

    catalog_track = Track(
        id="search:spotify:one-more-time",
        title="One More Time",
        artists=(Artist("Daft Punk"),),
        provider=MusicProviderName.SPOTIFY,
        provider_track_id="one-more-time",
    )
    recognized = RecognitionResult(
        TrackCandidate(catalog_track, "spotify"), 0.95, RecognitionDecision.ACCEPT
    )
    resolver = _CanonicalResolver(track_id, [])
    requests = TelegramTrackRequestService(database, _UnusedTrackResolver(), telegram_bot_id=100)
    downloads = DownloadService(
        DownloadTrackUseCase(resolver, ExistingDeliverySubmissionService(database, requests)),
        token_factory=lambda: "b" * 24,
    )

    context = TelegramContext(user.telegram_id, user.telegram_id, TelegramChatType.PRIVATE)
    confirmation = downloads.create_confirmation(context=context, result=recognized)
    assert confirmation is not None
    submission = await downloads.confirm(
        context=context,
        token=confirmation.token,
        target=DownloadDeliveryTarget(
            user.telegram_id,
            context,
            PrivateUserTarget(user.telegram_id),
            700,
        ),
    )
    assert submission is not None
    assert submission.state is DownloadSubmissionState.QUEUED
    assert resolver.calls == [catalog_track]

    gateway = _Gateway()
    wake = asyncio.Event()
    delivery_worker = TelegramDeliveryWorker(
        database,
        DeliveryPreparationService(database, telegram_bot_id=100, max_size=1000),
        TelegramFileCacheService(database),
        gateway,
        LocalizationService(("en", "ru"), "en"),
        max_attempts=3,
        wake_event=wake,
    )
    pending_delivery = await delivery_worker.claim("delivery-admission")
    assert pending_delivery is not None
    await delivery_worker.process(pending_delivery, "delivery-admission")

    async with database.transaction() as repositories:
        admitted = await repositories.telegram_delivery.get(submission.delivery_request_id)
        assert admitted is not None
        assert admitted.status is TelegramDeliveryStatus.WAITING
        assert admitted.download_job_id is not None

    artifacts = DownloadArtifactManager(tmp_path / "artifacts")
    notifier = SubscriberNotifier()
    pipeline = _Pipeline(artifacts, source_id)
    resolver_spy = _Stage25ResolverSpy()
    ranker_spy = _Stage25RankerSpy()
    downloader = DownloadWorkerBackend(
        database,
        pipeline,
        artifacts,
        subscriber_notifier=notifier,
        candidate_resolver=resolver_spy,  # type: ignore[arg-type]
        candidate_ranker=ranker_spy,  # type: ignore[arg-type]
    )
    download_job = await downloader.claim("stage18-download")
    assert download_job is not None
    await downloader.process(download_job, "stage18-download")

    uploads = UploadQueueService(database, artifacts, subscriber_notifier=notifier)
    uploader = UploadWorkerBackend(
        database,
        TelegramCacheUploadExecutor(
            database, TelegramFileCacheService(database), gateway, cache_chat_id=-100123
        ),
        uploads,
        subscriber_notifier=notifier,
    )
    upload_job = await uploader.claim("stage18-upload")
    assert upload_job is not None
    await uploader.process(upload_job, "stage18-upload")

    ready_delivery = await delivery_worker.claim("delivery-send")
    assert ready_delivery is not None
    await delivery_worker.process(ready_delivery, "delivery-send")

    async with database.transaction() as repositories:
        completed = await repositories.telegram_delivery.get(submission.delivery_request_id)
        assert completed is not None
        assert completed.status is TelegramDeliveryStatus.DELIVERED
    assert pipeline.calls == 1
    assert resolver_spy.calls == 1
    assert ranker_spy.calls == 1
    assert gateway.sent == ["stage18-file-id"]
