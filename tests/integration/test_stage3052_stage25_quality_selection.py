"""Representative Stage 25 selections backed by the real QualityResolver."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.core.delivery_targets import PrivateUserTarget
from app.core.enums import (
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    ProviderResolutionStatus,
    ProviderRuntimeStatus,
    QualityProfile,
)
from app.core.models import DownloadProviderCandidate, NativeMediaInfo, ProviderResolutionResult
from app.core.provider_resolution import (
    CanonicalMediaIdentity,
    ProviderCandidate,
    ProviderCandidateRanker,
    match_media,
)
from app.providers.onthespot.capabilities import ONTHESPOT_CAPABILITIES
from app.services.artifacts import DownloadArtifactManager
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
