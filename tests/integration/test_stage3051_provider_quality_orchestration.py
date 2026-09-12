from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.delivery_targets import PrivateUserTarget
from app.core.enums import (
    DownloadFailureCode,
    DownloadPlanOperation,
    DownloadPlanReadiness,
    DownloadPlanReason,
    MusicProviderName,
    QualityProfile,
    QualityResolutionStatus,
)
from app.core.models import DownloadPlan, QualityResolutionResult, SourceMediaRequirement
from app.core.provider_resolution import ProviderCandidateRanker
from app.core.quality import QUALITY_OUTPUTS
from app.services.artifacts import DownloadArtifactManager
from app.services.stage25_execution import Stage25DownloadExecutor
from app.services.workers import DownloadWorkerBackend
from app.storage import Database
from app.storage.models.base import utc_now

from .stage25_test_support import (
    _Accounts,
    _candidate,
    _Candidates,
    _ExactPipeline,
)


class _QualitySnapshot:
    def __init__(self, plans: tuple[DownloadPlan, ...]) -> None:
        self._result = QualityResolutionResult(
            track_id=0,
            requested_profile=QualityProfile.AAC_128,
            status=QualityResolutionStatus.RESOLVED,
            plans=plans,
            provider_diagnostics=(),
            resolved_at=datetime.now(UTC),
        )

    async def resolve(
        self, track_id: int, quality_profile: QualityProfile
    ) -> QualityResolutionResult:
        return self._result


def _plan(
    provider: MusicProviderName,
    provider_track_id: str,
    track_source_id: int,
    operation: DownloadPlanOperation,
) -> DownloadPlan:
    return DownloadPlan(
        track_id=1,
        track_source_id=track_source_id,
        provider=provider,
        provider_track_id=provider_track_id,
        requested_profile=QualityProfile.AAC_128,
        source_expectation=(
            SourceMediaRequirement(required_lossless=True)
            if operation is DownloadPlanOperation.TRANSCODE
            else SourceMediaRequirement(
                required_codec=QUALITY_OUTPUTS[QualityProfile.AAC_128].codec,
                required_bitrate_kbps=128,
            )
        ),
        output_specification=QUALITY_OUTPUTS[QualityProfile.AAC_128],
        operation=operation,
        readiness=DownloadPlanReadiness.CONFIRMED,
        reason=(
            DownloadPlanReason.LOSSLESS_TO_REQUESTED_LOSSY
            if operation is DownloadPlanOperation.TRANSCODE
            else DownloadPlanReason.NATIVE_EXACT_MATCH
        ),
    )


async def _admit(database: Database) -> tuple[int, int]:
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(30501)
        track = await repositories.tracks.create_track(title="Stage 30.5.1", artist="Orchestration")
        await repositories.track_sources.upsert_source(
            track_id=track.id,
            provider=MusicProviderName.QOBUZ,
            provider_track_id="qobuz-flac",
            url="https://example.test/qobuz-flac",
        )
        ytm = await repositories.track_sources.upsert_source(
            track_id=track.id,
            provider=MusicProviderName.YOUTUBE_MUSIC,
            provider_track_id="ytm-aac128",
            url="https://music.youtube.com/watch?v=ytm-aac128",
        )
        user_id = user.id
        telegram_id = user.telegram_id
        track_id = track.id
    async with database.transaction() as repositories:
        await repositories.download_lifecycle.admit(
            requester_user_id=user_id,
            confirmation_id=f"stage3051-{track_id}",
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
            track_id=track_id,
            quality_profile=QualityProfile.AAC_128,
            max_active=10,
            now=utc_now(),
        )
    return job.id, ytm.source.id


@pytest.mark.asyncio
async def test_stage25_global_quality_order_beats_recognition_affinity(
    database: Database, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    job_id, ytm_source_id = await _admit(database)
    artifacts = DownloadArtifactManager(tmp_path / "artifacts")
    pipeline = _ExactPipeline(
        artifacts,
        {
            (MusicProviderName.QOBUZ, "qobuz-flac"): None,
            (MusicProviderName.YOUTUBE_MUSIC, "ytm-aac128"): None,
        },
        ytm_source_id,
    )
    plans = (
        _plan(MusicProviderName.QOBUZ, "qobuz-flac", 1, DownloadPlanOperation.TRANSCODE),
        _plan(
            MusicProviderName.YOUTUBE_MUSIC,
            "ytm-aac128",
            ytm_source_id,
            DownloadPlanOperation.DIRECT,
        ),
    )
    executor = Stage25DownloadExecutor(
        database,
        pipeline,
        _Accounts(
            {
                MusicProviderName.QOBUZ: ("qobuz-account",),
                MusicProviderName.YOUTUBE_MUSIC: ("ytm-account",),
            }
        ),
        _Candidates(
            (
                _candidate(MusicProviderName.QOBUZ, "qobuz-flac"),
                _candidate(MusicProviderName.YOUTUBE_MUSIC, "ytm-aac128"),
            )
        ),
        ProviderCandidateRanker(),
        quality_resolver=_QualitySnapshot(plans),  # type: ignore[arg-type]
    )
    backend = DownloadWorkerBackend(database, pipeline, artifacts, stage25_executor=executor)
    job = await backend.claim("stage3051-quality")
    assert job is not None and job.id == job_id
    await backend.process(job, "stage3051-quality")

    assert pipeline.calls == [(MusicProviderName.YOUTUBE_MUSIC, "ytm-account")]


@pytest.mark.asyncio
async def test_stage25_direct_failure_falls_back_to_next_quality_plan(
    database: Database, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    _, ytm_source_id = await _admit(database)
    artifacts = DownloadArtifactManager(tmp_path / "artifacts")
    pipeline = _ExactPipeline(
        artifacts,
        {
            (MusicProviderName.YOUTUBE_MUSIC, "ytm-account"): DownloadFailureCode.MEDIA_UNAVAILABLE,
            (MusicProviderName.QOBUZ, "qobuz-flac"): None,
        },
        ytm_source_id,
    )
    plans = (
        _plan(MusicProviderName.QOBUZ, "qobuz-flac", 1, DownloadPlanOperation.TRANSCODE),
        _plan(
            MusicProviderName.YOUTUBE_MUSIC,
            "ytm-aac128",
            ytm_source_id,
            DownloadPlanOperation.DIRECT,
        ),
    )
    executor = Stage25DownloadExecutor(
        database,
        pipeline,
        _Accounts(
            {
                MusicProviderName.QOBUZ: ("qobuz-account",),
                MusicProviderName.YOUTUBE_MUSIC: ("ytm-account",),
            }
        ),
        _Candidates(
            (
                _candidate(MusicProviderName.QOBUZ, "qobuz-flac"),
                _candidate(MusicProviderName.YOUTUBE_MUSIC, "ytm-aac128"),
            )
        ),
        ProviderCandidateRanker(),
        quality_resolver=_QualitySnapshot(plans),  # type: ignore[arg-type]
    )
    backend = DownloadWorkerBackend(database, pipeline, artifacts, stage25_executor=executor)
    job = await backend.claim("stage3051-fallback")
    assert job is not None
    await backend.process(job, "stage3051-fallback")

    assert pipeline.calls == [
        (MusicProviderName.YOUTUBE_MUSIC, "ytm-account"),
        (MusicProviderName.QOBUZ, "qobuz-account"),
    ]


@pytest.mark.asyncio
async def test_stage25_same_provider_account_fallback_precedes_lower_quality_provider(
    database: Database, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    _, ytm_source_id = await _admit(database)
    artifacts = DownloadArtifactManager(tmp_path / "artifacts")
    pipeline = _ExactPipeline(
        artifacts,
        {
            (MusicProviderName.YOUTUBE_MUSIC, "ytm-a1"): DownloadFailureCode.PROVIDER_AUTH,
            (MusicProviderName.YOUTUBE_MUSIC, "ytm-a2"): None,
            (MusicProviderName.QOBUZ, "qobuz-account"): None,
        },
        ytm_source_id,
    )
    plans = (
        _plan(MusicProviderName.QOBUZ, "qobuz-flac", 1, DownloadPlanOperation.TRANSCODE),
        _plan(
            MusicProviderName.YOUTUBE_MUSIC,
            "ytm-aac128",
            ytm_source_id,
            DownloadPlanOperation.DIRECT,
        ),
    )
    executor = Stage25DownloadExecutor(
        database,
        pipeline,
        _Accounts(
            {
                MusicProviderName.QOBUZ: ("qobuz-account",),
                MusicProviderName.YOUTUBE_MUSIC: ("ytm-a1", "ytm-a2"),
            }
        ),
        _Candidates(
            (
                _candidate(MusicProviderName.QOBUZ, "qobuz-flac"),
                _candidate(MusicProviderName.YOUTUBE_MUSIC, "ytm-aac128"),
            )
        ),
        ProviderCandidateRanker(),
        quality_resolver=_QualitySnapshot(plans),  # type: ignore[arg-type]
    )
    backend = DownloadWorkerBackend(database, pipeline, artifacts, stage25_executor=executor)
    job = await backend.claim("stage3051-accounts")
    assert job is not None
    await backend.process(job, "stage3051-accounts")

    assert pipeline.calls == [
        (MusicProviderName.YOUTUBE_MUSIC, "ytm-a1"),
        (MusicProviderName.YOUTUBE_MUSIC, "ytm-a2"),
    ]


@pytest.mark.asyncio
async def test_stage25_processing_failure_does_not_try_the_next_provider(
    database: Database, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    _, ytm_source_id = await _admit(database)
    artifacts = DownloadArtifactManager(tmp_path / "artifacts")
    pipeline = _ExactPipeline(
        artifacts,
        {
            (MusicProviderName.YOUTUBE_MUSIC, "ytm-account"): DownloadFailureCode.PROCESSING,
            (MusicProviderName.QOBUZ, "qobuz-account"): None,
        },
        ytm_source_id,
    )
    plans = (
        _plan(MusicProviderName.QOBUZ, "qobuz-flac", 1, DownloadPlanOperation.TRANSCODE),
        _plan(
            MusicProviderName.YOUTUBE_MUSIC,
            "ytm-aac128",
            ytm_source_id,
            DownloadPlanOperation.DIRECT,
        ),
    )
    executor = Stage25DownloadExecutor(
        database,
        pipeline,
        _Accounts(
            {
                MusicProviderName.QOBUZ: ("qobuz-account",),
                MusicProviderName.YOUTUBE_MUSIC: ("ytm-account",),
            }
        ),
        _Candidates(
            (
                _candidate(MusicProviderName.QOBUZ, "qobuz-flac"),
                _candidate(MusicProviderName.YOUTUBE_MUSIC, "ytm-aac128"),
            )
        ),
        ProviderCandidateRanker(),
        quality_resolver=_QualitySnapshot(plans),  # type: ignore[arg-type]
    )
    backend = DownloadWorkerBackend(database, pipeline, artifacts, stage25_executor=executor)
    job = await backend.claim("stage3051-processing")
    assert job is not None
    await backend.process(job, "stage3051-processing")

    assert pipeline.calls == [(MusicProviderName.YOUTUBE_MUSIC, "ytm-account")]
