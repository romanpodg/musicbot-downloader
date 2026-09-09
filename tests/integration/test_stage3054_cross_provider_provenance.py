from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete

from app.core.delivery_targets import PrivateUserTarget
from app.core.download_preferences import EffectiveDownloadProfile
from app.core.enums import (
    DeliveryMode,
    DownloadFailureCode,
    DownloadPlanOperation,
    DownloadPlanReadiness,
    FormatPreference,
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    QualityPreference,
    QualityProfile,
    TelegramMediaKind,
)
from app.core.exceptions import DownloadPipelineError
from app.core.media_artifact import MediaArtifactSpec
from app.core.models import (
    DownloadResult,
    PreparedSourceMedia,
    ProviderMediaCapabilities,
    TelegramBotIdentity,
    TelegramUploadReceipt,
)
from app.core.provider_resolution import (
    CanonicalMediaIdentity,
    ProviderCandidate,
    ProviderCandidateRanker,
    match_media,
)
from app.core.telegram_artifact_cache import TelegramCacheKey
from app.services.artifacts import DownloadArtifactManager
from app.services.queues import UploadQueueService
from app.services.singleflight import SingleFlightService, SubscriberNotifier
from app.services.stage25_execution import Stage25DownloadExecutor
from app.services.telegram_artifact_cache import TelegramArtifactCacheService
from app.services.telegram_cache import TelegramFileCacheService
from app.services.telegram_upload import TelegramCacheUploadExecutor
from app.services.workers import DownloadWorkerBackend, UploadWorkerBackend
from app.storage import Database
from app.storage.models import TrackSource
from app.storage.models.base import utc_now


def _profile() -> EffectiveDownloadProfile:
    return EffectiveDownloadProfile(
        requested_quality=QualityPreference.HIGH,
        effective_quality=QualityPreference.HIGH,
        requested_format=FormatPreference.M4A,
        effective_format=FormatPreference.M4A,
        delivery_mode=DeliveryMode.AUDIO,
        embed_metadata=True,
        embed_cover=True,
    )


@dataclass
class _Candidates:
    values: tuple[ProviderCandidate, ...]
    started: asyncio.Event
    release: asyncio.Event
    identity: CanonicalMediaIdentity | None = None

    async def resolve(self, identity, **kwargs):  # type: ignore[no-untyped-def]
        self.identity = identity
        self.started.set()
        await self.release.wait()
        return self.values


@dataclass
class _Accounts:
    values: dict[MusicProviderName, tuple[str, ...]]

    async def list_provider_accounts(self, provider: MusicProviderName) -> tuple[str, ...]:
        return self.values.get(provider, ())


class _FallbackPipeline:
    def __init__(self, artifacts: DownloadArtifactManager, source_id: int) -> None:
        self._artifacts = artifacts
        self._source_id = source_id
        self.calls: list[MusicProviderName] = []

    async def download_selected(
        self,
        track_id: int,
        quality_profile: QualityProfile,
        *,
        provider: MusicProviderName,
        provider_media_id: str,
        account_id: str | None = None,
        profile: EffectiveDownloadProfile | None = None,
    ) -> DownloadResult:
        self.calls.append(provider)
        if provider is MusicProviderName.APPLE_MUSIC:
            raise DownloadPipelineError(DownloadFailureCode.MEDIA_UNAVAILABLE)
        artifact_job_id, _ = self._artifacts.create_job()
        path = self._artifacts.final_path(artifact_job_id, "m4a")
        path.write_bytes(b"stage3054-qobuz-aac")
        source = PreparedSourceMedia(
            provider,
            provider_media_id,
            codec=NativeCodec.FLAC,
            container=NativeContainer.FLAC,
            bitrate_kbps=1000,
            lossless=True,
        )
        output = PreparedSourceMedia(
            provider,
            provider_media_id,
            codec=NativeCodec.AAC,
            container=NativeContainer.M4A,
            bitrate_kbps=256,
            lossless=False,
            file_path=path,
        )
        return DownloadResult(
            artifact_job_id,
            track_id,
            quality_profile,
            self._source_id,
            provider,
            provider_media_id,
            DownloadPlanOperation.TRANSCODE,
            DownloadPlanReadiness.CONFIRMED,
            source,
            output,
            path,
            path.stat().st_size,
            True,
            1,
            (),
            datetime.now(UTC),
            "aac",
        )


class _Gateway:
    async def get_bot_identity(self) -> TelegramBotIdentity:
        return TelegramBotIdentity(3054, "stage3054_bot")

    async def upload_audio(self, spec) -> TelegramUploadReceipt:  # type: ignore[no-untyped-def]
        return TelegramUploadReceipt(
            3054,
            -1003054,
            1,
            TelegramMediaKind.AUDIO,
            "qobuz-file",
            "qobuz-unique",
            spec.file_path.stat().st_size,
        )

    async def upload_document(self, spec) -> TelegramUploadReceipt:  # type: ignore[no-untyped-def]
        raise AssertionError("AAC/M4A artifact must retain its existing audio-cache behavior")


def _candidate(provider: MusicProviderName, media_id: str) -> ProviderCandidate:
    identity = CanonicalMediaIdentity.from_values(
        title="Canonical", artist="Artist", isrc="US3054000001"
    )
    return ProviderCandidate(
        provider,
        media_id,
        identity,
        match_media(identity, identity),
        ProviderMediaCapabilities(supports_lossy=True),
    )


async def _admit(
    database: Database,
    *,
    track_id: int,
    user_id: int,
    telegram_id: int,
    provider: MusicProviderName,
    media_id: str,
    confirmation: str,
):
    async with database.transaction() as repositories:
        return await repositories.download_lifecycle.admit(
            requester_user_id=user_id,
            confirmation_id=confirmation,
            source_type="DIRECT_URL",
            source_reference=str(track_id),
            provider=provider.value,
            provider_media_id=media_id,
            media_title="Canonical",
            media_artist="Artist",
            delivery_target_type=PrivateUserTarget(telegram_id).target_type,
            delivery_target_id=telegram_id,
            now=utc_now(),
            profile=_profile(),
        )


async def test_stage3054_apple_admission_qobuz_fallback_preserves_all_identities(
    database: Database, tmp_path: Path
) -> None:
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(3054001)
        track = await repositories.tracks.create_track(
            title="Canonical", artist="Artist", isrc="US3054000001"
        )
        qobuz_source = await repositories.track_sources.upsert_source(
            track_id=track.id,
            provider=MusicProviderName.QOBUZ,
            provider_track_id="qobuz-actual",
            url="https://example.test/qobuz-actual",
        )
    apple_request, apple_lifecycle, apple_delivery = await _admit(
        database,
        track_id=track.id,
        user_id=user.id,
        telegram_id=user.telegram_id,
        provider=MusicProviderName.APPLE_MUSIC,
        media_id="apple-admitted",
        confirmation="stage3054-apple",
    )
    fingerprint = MediaArtifactSpec.from_profile(
        _profile(), metadata={"title": "Canonical", "artist": "Artist"}
    ).fingerprint
    artifacts = DownloadArtifactManager(tmp_path / "artifacts")
    notifier = SubscriberNotifier()
    uploads = UploadQueueService(database, artifacts, subscriber_notifier=notifier)
    singleflight = SingleFlightService(
        database, max_size=10, notifier=notifier, upload_queue=uploads
    )
    leader = await singleflight.submit(
        track_id=track.id,
        quality_profile=QualityProfile.AAC_256,
        artifact_fingerprint=fingerprint,
        request_key="apple-leader",
    )
    started, release = asyncio.Event(), asyncio.Event()
    candidates = _Candidates(
        (
            _candidate(MusicProviderName.APPLE_MUSIC, "apple-admitted"),
            _candidate(MusicProviderName.QOBUZ, "qobuz-actual"),
        ),
        started,
        release,
    )
    pipeline = _FallbackPipeline(artifacts, qobuz_source.source.id)
    executor = Stage25DownloadExecutor(
        database,
        pipeline,
        _Accounts(
            {
                MusicProviderName.APPLE_MUSIC: ("apple-account",),
                MusicProviderName.QOBUZ: ("qobuz-account",),
            }
        ),
        candidates,  # type: ignore[arg-type]
        ProviderCandidateRanker((MusicProviderName.APPLE_MUSIC, MusicProviderName.QOBUZ)),
    )
    downloader = DownloadWorkerBackend(
        database, pipeline, artifacts, stage25_executor=executor, subscriber_notifier=notifier
    )
    job = await downloader.claim("stage3054-download")
    assert job is not None
    processing = asyncio.create_task(downloader.process(job, "stage3054-download"))
    await asyncio.wait_for(started.wait(), timeout=1)
    assert candidates.identity is not None

    qobuz_request, _, _ = await _admit(
        database,
        track_id=track.id,
        user_id=user.id,
        telegram_id=user.telegram_id,
        provider=MusicProviderName.QOBUZ,
        media_id="qobuz-admitted",
        confirmation="stage3054-qobuz",
    )
    follower = await singleflight.submit(
        track_id=track.id,
        quality_profile=QualityProfile.AAC_256,
        artifact_fingerprint=fingerprint,
        request_key="qobuz-follower",
    )
    assert follower.download_job.id == leader.download_job.id
    assert follower.joined_existing_flight
    release.set()
    await processing

    apple_key = TelegramCacheKey.from_request(apple_request)
    qobuz_key = TelegramCacheKey.from_request(qobuz_request)
    assert apple_key is not None and qobuz_key is not None
    assert apple_key.provider == MusicProviderName.APPLE_MUSIC.value
    assert apple_key.provider_media_id == "apple-admitted"
    assert apple_key.fingerprint != qobuz_key.fingerprint
    assert pipeline.calls == [MusicProviderName.APPLE_MUSIC, MusicProviderName.QOBUZ]

    async with database.transaction() as repositories:
        attempts = await repositories.provider_resolution.list_attempts(apple_lifecycle.id)
        successful_provider = await repositories.provider_resolution.successful_provider(
            apple_lifecycle.id
        )
        upload = next(
            (
                item
                for item in await repositories.upload_jobs.list(offset=0, limit=10)
                if item.download_job_id == job.id
            ),
            None,
        )
    assert [attempt.status for attempt in attempts] == ["FAILED", "SUCCEEDED"]
    assert [attempt.provider_account_id for attempt in attempts] == [
        "apple-account",
        "qobuz-account",
    ]
    assert successful_provider == MusicProviderName.QOBUZ.value
    assert upload is not None
    assert upload.source_provider is MusicProviderName.QOBUZ
    assert upload.source_provider_track_id == "qobuz-actual"

    cache = TelegramFileCacheService(database)
    uploader = UploadWorkerBackend(
        database,
        TelegramCacheUploadExecutor(database, cache, _Gateway(), cache_chat_id=-1003054),
        uploads,
        subscriber_notifier=notifier,
    )
    upload_job = await uploader.claim("stage3054-upload")
    assert upload_job is not None
    await uploader.process(upload_job, "stage3054-upload")
    cached = await cache.get_active(
        telegram_bot_id=3054,
        track_id=track.id,
        quality_profile=QualityProfile.AAC_256,
        artifact_fingerprint=fingerprint,
    )
    assert cached is not None
    assert cached.source_provider is MusicProviderName.QOBUZ
    assert cached.source_provider_track_id == "qobuz-actual"

    artifact_cache = TelegramArtifactCacheService(database)
    recorded = await artifact_cache.record_successful_delivery(
        apple_request, delivery_id=apple_delivery.id, file_id=cached.file_id
    )
    assert recorded is not None and recorded.provider == MusicProviderName.APPLE_MUSIC.value
    assert await artifact_cache.lookup(qobuz_request) is None

    async with database.engine.begin() as connection:
        await connection.execute(
            delete(TrackSource).where(TrackSource.id == qobuz_source.source.id)
        )
    durable = await cache.get(cached.cache_id)
    assert durable.source_track_source_id is None
    assert durable.source_provider is MusicProviderName.QOBUZ
    assert durable.source_provider_track_id == "qobuz-actual"
