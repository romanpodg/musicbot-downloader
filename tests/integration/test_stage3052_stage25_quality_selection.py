"""Representative Stage 25 selections backed by the real QualityResolver."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.core.delivery_targets import PrivateUserTarget
from app.core.download import DownloadDeliveryTarget, DownloadRequest
from app.core.download_preferences import UserDownloadPreferences
from app.core.enums import (
    BatchSourceType,
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    ProviderResolutionStatus,
    ProviderRuntimeStatus,
    QualityPreference,
    QualityProfile,
)
from app.core.models import (
    DownloadProviderCandidate,
    NativeMediaInfo,
    ProviderResolutionResult,
    ResolvedCollection,
    ResolvedCollectionItem,
)
from app.core.provider_resolution import (
    CanonicalMediaIdentity,
    CanonicalTrackAdmission,
    ProviderCandidate,
    ProviderCandidateRanker,
    match_media,
)
from app.core.telegram_context import TelegramChatType, TelegramContext
from app.providers.onthespot.capabilities import ONTHESPOT_CAPABILITIES
from app.services.artifacts import DownloadArtifactManager
from app.services.batch_download import BatchDownloadService
from app.services.download_lifecycle import DownloadLifecycleService
from app.services.quality_resolution import QualityResolver
from app.services.stage25_execution import Stage25DownloadExecutor
from app.services.workers import DownloadWorkerBackend
from app.storage import Database
from app.storage.models.base import utc_now

from .stage25_test_support import _Accounts, _ExactPipeline


@dataclass
class _Stage25Candidates:
    values: tuple[ProviderCandidate, ...]

    async def resolve(
        self, identity: CanonicalMediaIdentity, **kwargs: object
    ) -> tuple[ProviderCandidate, ...]:
        return self.values


@dataclass
class _QualityProviderSnapshot:
    values: tuple[DownloadProviderCandidate, ...]

    async def resolve(self, track_id: int) -> ProviderResolutionResult:
        return ProviderResolutionResult(
            track_id,
            ProviderResolutionStatus.AVAILABLE,
            self.values,
            (),
        )


def _media_candidate(
    provider: MusicProviderName,
    media_id: str,
    source_id: int,
    native: NativeMediaInfo,
) -> DownloadProviderCandidate:
    return DownloadProviderCandidate(
        1,
        source_id,
        provider,
        media_id,
        ProviderRuntimeStatus.AVAILABLE,
        ONTHESPOT_CAPABILITIES[provider],
        native,
    )


def _stage25_candidate(provider: MusicProviderName, media_id: str) -> ProviderCandidate:
    identity = CanonicalMediaIdentity.from_values(
        title="Stage 30.5.2", artist="Quality Matrix", isrc="USABC1234567"
    )
    return ProviderCandidate(
        provider,
        media_id,
        identity,
        match_media(identity, identity),
        ONTHESPOT_CAPABILITIES[provider].media,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile", "candidates", "expected", "accounts"),
    [
        (
            QualityProfile.AAC_128,
            (
                (
                    MusicProviderName.QOBUZ,
                    "qobuz-flac",
                    NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
                ),
                (
                    MusicProviderName.YOUTUBE_MUSIC,
                    "ytm-aac128",
                    NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 128),
                ),
            ),
            MusicProviderName.YOUTUBE_MUSIC,
            {MusicProviderName.QOBUZ: ("qobuz-account",)},
        ),
        (
            QualityProfile.AAC_256,
            (
                (
                    MusicProviderName.QOBUZ,
                    "qobuz-flac",
                    NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
                ),
                (
                    MusicProviderName.APPLE_MUSIC,
                    "apple-aac256",
                    NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 256),
                ),
            ),
            MusicProviderName.APPLE_MUSIC,
            {
                MusicProviderName.QOBUZ: ("qobuz-account",),
                MusicProviderName.APPLE_MUSIC: ("apple-account",),
            },
        ),
        (
            QualityProfile.LOSSLESS,
            (
                (
                    MusicProviderName.APPLE_MUSIC,
                    "apple-aac256",
                    NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 256),
                ),
                (
                    MusicProviderName.QOBUZ,
                    "qobuz-flac",
                    NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
                ),
            ),
            MusicProviderName.QOBUZ,
            {
                MusicProviderName.QOBUZ: ("qobuz-account",),
                MusicProviderName.APPLE_MUSIC: ("apple-account",),
            },
        ),
        (
            QualityProfile.AAC_256,
            (
                (
                    MusicProviderName.YOUTUBE_MUSIC,
                    "ytm-aac128",
                    NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 128),
                ),
                (
                    MusicProviderName.QOBUZ,
                    "qobuz-flac",
                    NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
                ),
            ),
            MusicProviderName.QOBUZ,
            {MusicProviderName.QOBUZ: ("qobuz-account",)},
        ),
        (
            QualityProfile.MP3_320,
            (
                (
                    MusicProviderName.SPOTIFY,
                    "spotify-vorbis",
                    NativeMediaInfo(NativeCodec.VORBIS, NativeContainer.OGG, 320),
                ),
                (
                    MusicProviderName.QOBUZ,
                    "qobuz-flac",
                    NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
                ),
            ),
            MusicProviderName.QOBUZ,
            {MusicProviderName.QOBUZ: ("qobuz-account",)},
        ),
    ],
)
async def test_stage25_uses_complete_quality_matrix_before_affinity(
    database: Database,
    tmp_path,
    profile: QualityProfile,
    candidates: tuple[tuple[MusicProviderName, str, NativeMediaInfo], ...],
    expected: MusicProviderName,
    accounts: dict[MusicProviderName, tuple[str, ...]],
) -> None:  # type: ignore[no-untyped-def]
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(30502)
        track = await repositories.tracks.create_track(
            title="Stage 30.5.2", artist="Quality Matrix"
        )
        await repositories.track_sources.upsert_source(
            track_id=track.id,
            provider=MusicProviderName.QOBUZ,
            provider_track_id="qobuz-flac",
            url="https://example.test/qobuz-flac",
        )
        track_id = track.id
        user_id = user.id
        telegram_id = user.telegram_id
    async with database.transaction() as repositories:
        await repositories.download_lifecycle.admit(
            requester_user_id=user_id,
            confirmation_id=f"stage3052-{track_id}",
            source_type="DIRECT_URL",
            source_reference=str(track_id),
            provider=MusicProviderName.QOBUZ.value,
            provider_media_id="qobuz-flac",
            delivery_target_type=PrivateUserTarget(telegram_id).target_type,
            delivery_target_id=telegram_id,
            now=utc_now(),
        )
    async with database.transaction() as repositories:
        job = await repositories.download_jobs.submit(
            track_id=track_id, quality_profile=profile, max_active=10, now=utc_now()
        )

    quality_candidates = tuple(
        _media_candidate(provider, media_id, index + 1, native)
        for index, (provider, media_id, native) in enumerate(candidates)
    )
    stage_candidates = tuple(
        _stage25_candidate(provider, media_id) for provider, media_id, _ in candidates
    )
    outcome_keys = {
        (provider, account): None
        for provider, provider_accounts in accounts.items()
        for account in provider_accounts
    }
    outcome_keys.update({(MusicProviderName.YOUTUBE_MUSIC, None): None})
    pipeline = _ExactPipeline(DownloadArtifactManager(tmp_path / "artifacts"), outcome_keys, 1)
    executor = Stage25DownloadExecutor(
        database,
        pipeline,
        _Accounts(accounts),
        _Stage25Candidates(stage_candidates),
        ProviderCandidateRanker(),
        quality_resolver=QualityResolver(_QualityProviderSnapshot(quality_candidates)),
    )
    backend = DownloadWorkerBackend(
        database,
        pipeline,
        DownloadArtifactManager(tmp_path / "worker-artifacts"),
        stage25_executor=executor,
    )

    claimed = await backend.claim(f"stage3052-{profile.value}")
    assert claimed is not None and claimed.id == job.id
    await executor.download(claimed)

    assert pipeline.calls == [
        (expected, accounts.get(expected, (None,))[0] if accounts.get(expected) else None)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("collection_provider", "collection_id", "source_reference", "collection_item", "native"),
    [
        (
            MusicProviderName.APPLE_MUSIC,
            "apple-album",
            "https://music.apple.com/us/album/release/apple-album",
            "apple-item",
            NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 256),
        ),
        (
            MusicProviderName.YOUTUBE_MUSIC,
            "PLabc_1234567890",
            "https://music.youtube.com/playlist?list=PLabc_1234567890",
            "ytm-video",
            NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 128),
        ),
    ],
)
async def test_collection_admission_executes_lossless_from_qobuz_without_provider_affinity(
    database: Database,
    tmp_path,
    collection_provider: MusicProviderName,
    collection_id: str,
    source_reference: str,
    collection_item: str,
    native: NativeMediaInfo,
) -> None:  # type: ignore[no-untyped-def]
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(30_620)
        track = await repositories.tracks.create_track(
            title="Cross-provider recording", artist="Stage 30.6.2", isrc="USABC1234567"
        )
        source = await repositories.track_sources.upsert_source(
            track_id=track.id,
            provider=MusicProviderName.QOBUZ,
            provider_track_id="qobuz-lossless",
            url="https://example.test/qobuz-lossless",
        )
    admission = CanonicalTrackAdmission(
        track.id,
        CanonicalMediaIdentity.from_values(
            title="Cross-provider recording", artist="Stage 30.6.2", isrc="USABC1234567"
        ),
    )
    lifecycle = DownloadLifecycleService(database)

    @dataclass
    class AdmissionResolver:
        async def resolve(self, **_: object) -> CanonicalTrackAdmission:
            return admission

    async def child(request: DownloadRequest, *, target: DownloadDeliveryTarget):
        durable = await lifecycle.admit(
            confirmation_id=request.confirmation_id or "missing",
            request=request,
            canonical_track_id=track.id,
            target=target,
        )
        return SimpleNamespace(request_id=durable.request.id)

    collection = ResolvedCollection(
        source_type=BatchSourceType.PLAYLIST,
        provider=collection_provider,
        collection_id=collection_id,
        source_reference=source_reference,
        title="Discovery only",
        creator="Collection creator",
        items=(ResolvedCollectionItem(1, collection_item),),
    )
    batches = BatchDownloadService(
        database,
        SimpleNamespace(resolve_collection=None),
        child_admitter=child,
        item_admission_resolver=AdmissionResolver(),
    )
    batch = await batches.create_from_collection(
        user_id=user.id,
        confirmation_id="stage3062-apple-qobuz",
        collection=collection,
        preferences=UserDownloadPreferences(user.id, quality=QualityPreference.LOSSLESS),
    )
    target = DownloadDeliveryTarget(
        user_id=user.telegram_id,
        context=TelegramContext(user.telegram_id, user.telegram_id, TelegramChatType.PRIVATE),
        delivery_target=PrivateUserTarget(user.telegram_id),
        source_message_id=30_620,
    )
    assert await batches.admit_pending(batch.id, target=target) == 1

    async with database.transaction() as repositories:
        request = await repositories.download_lifecycle.get_by_confirmation(
            "stage3062-apple-qobuz:1"
        )
        assert (
            request is not None and request.provider is None and request.provider_media_id is None
        )
        job = await repositories.download_jobs.submit(
            track_id=track.id,
            quality_profile=QualityProfile.LOSSLESS,
            max_active=10,
            now=utc_now(),
        )
        durable_batch = await repositories.batch_download.get(batch.id)
    assert durable_batch is not None and durable_batch.provider is collection_provider

    identity = admission.identity
    stage_candidates = (
        ProviderCandidate(
            collection_provider,
            "discovery-provider-source",
            identity,
            match_media(identity, identity),
            ONTHESPOT_CAPABILITIES[MusicProviderName.APPLE_MUSIC].media,
        ),
        ProviderCandidate(
            MusicProviderName.QOBUZ,
            "qobuz-lossless",
            identity,
            match_media(identity, identity),
            ONTHESPOT_CAPABILITIES[MusicProviderName.QOBUZ].media,
        ),
    )
    quality = _QualityProviderSnapshot(
        (
            _media_candidate(
                collection_provider,
                "discovery-provider-source",
                1,
                native,
            ),
            _media_candidate(
                MusicProviderName.QOBUZ,
                "qobuz-lossless",
                source.source.id,
                NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
            ),
        )
    )
    artifacts = DownloadArtifactManager(tmp_path / "artifacts")
    pipeline = _ExactPipeline(
        artifacts, {(MusicProviderName.QOBUZ, "qobuz-account"): None}, source.source.id
    )
    executor = Stage25DownloadExecutor(
        database,
        pipeline,
        _Accounts({MusicProviderName.QOBUZ: ("qobuz-account",)}),
        _Stage25Candidates(stage_candidates),
        ProviderCandidateRanker(),
        quality_resolver=QualityResolver(quality),
    )
    result = await executor.download(job)

    assert result.provider is MusicProviderName.QOBUZ
    assert pipeline.calls == [(MusicProviderName.QOBUZ, "qobuz-account")]
    async with database.transaction() as repositories:
        lifecycle_job = await repositories.download_lifecycle.get_job_for_request(request.id)
        assert lifecycle_job is not None
        attempts = await repositories.provider_resolution.list_attempts(lifecycle_job.id)
        candidates = await repositories.provider_resolution.list_candidates(request.id)
    assert [attempt.status for attempt in attempts] == ["SUCCEEDED"]
    assert [(candidate.provider, candidate.provider_media_id) for candidate in candidates] == [
        (MusicProviderName.QOBUZ.value, "qobuz-lossless")
    ]
