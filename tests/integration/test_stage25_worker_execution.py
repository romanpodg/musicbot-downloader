from __future__ import annotations

import pytest

from app.core.delivery_targets import PrivateUserTarget
from app.core.enums import (
    DownloadFailureCode,
    MusicProviderName,
    QualityProfile,
)
from app.core.provider_resolution import ProviderCandidateRanker
from app.services.artifacts import DownloadArtifactManager
from app.services.stage25_execution import Stage25DownloadExecutor
from app.services.workers import DownloadWorkerBackend
from app.storage import Database
from app.storage.models.base import utc_now

from .stage25_test_support import _Accounts, _candidate, _Candidates, _ExactPipeline


async def _admit(database: Database) -> tuple[int, int]:
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(25001)
        track = await repositories.tracks.create_track(title="Stage 25", artist="Executor")
        source = await repositories.track_sources.upsert_source(
            track_id=track.id,
            provider=MusicProviderName.TIDAL,
            provider_track_id="tidal-source",
            url="https://example.test/tidal-source",
        )
        user_id = user.id
        telegram_id = user.telegram_id
        track_id = track.id
        source_id = source.source.id
    async with database.transaction() as repositories:
        await repositories.download_lifecycle.admit(
            requester_user_id=user_id,
            confirmation_id=f"stage25-{track_id}",
            source_type="DIRECT_URL",
            source_reference=str(track_id),
            provider=MusicProviderName.TIDAL.value,
            provider_media_id="tidal-source",
            delivery_target_type=PrivateUserTarget(telegram_id).target_type,
            delivery_target_id=telegram_id,
            now=utc_now(),
        )
    async with database.transaction() as repositories:
        technical = await repositories.download_jobs.submit(
            track_id=track_id,
            quality_profile=QualityProfile.MP3_320,
            max_active=10,
            now=utc_now(),
        )
        return technical.id, source_id


@pytest.mark.asyncio
async def test_worker_cross_provider_fallback_persists_real_execution_attempts(
    database: Database, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    _, source_id = await _admit(database)
    artifacts = DownloadArtifactManager(tmp_path / "artifacts")
    pipeline = _ExactPipeline(
        artifacts,
        {
            (MusicProviderName.TIDAL, "a1"): DownloadFailureCode.MEDIA_UNAVAILABLE,
            (MusicProviderName.DEEZER, "b1"): None,
        },
        source_id,
    )
    executor = Stage25DownloadExecutor(
        database,
        pipeline,
        _Accounts({MusicProviderName.TIDAL: ("a1",), MusicProviderName.DEEZER: ("b1",)}),
        _Candidates(
            (
                _candidate(MusicProviderName.TIDAL, "tidal-source"),
                _candidate(MusicProviderName.DEEZER, "deezer-source"),
            )
        ),  # type: ignore[arg-type]
        ProviderCandidateRanker((MusicProviderName.TIDAL, MusicProviderName.DEEZER)),
    )
    backend = DownloadWorkerBackend(database, pipeline, artifacts, stage25_executor=executor)
    job = await backend.claim("stage25-cross")
    assert job is not None
    await backend.process(job, "stage25-cross")
    async with database.transaction() as repositories:
        current_job = await repositories.download_jobs.get(job.id)
    assert current_job is not None, "job disappeared"
    assert current_job.last_error_code is None, current_job.last_error_code

    assert pipeline.calls == [(MusicProviderName.TIDAL, "a1"), (MusicProviderName.DEEZER, "b1")]
    async with database.transaction() as repositories:
        request = await repositories.download_lifecycle.latest_request_for_track(job.track_id)
        assert request is not None
        lifecycle_job = await repositories.download_lifecycle.get_job_for_request(request.id)
        assert lifecycle_job is not None
        attempts = await repositories.provider_resolution.list_attempts(lifecycle_job.id)
        assert len(attempts) == 2
        assert [item.status for item in attempts] == ["FAILED", "SUCCEEDED"]
        assert [item.provider_account_id for item in attempts] == ["a1", "b1"]


@pytest.mark.asyncio
async def test_worker_same_provider_auth_failure_uses_next_healthy_account(
    database: Database, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    _, source_id = await _admit(database)
    artifacts = DownloadArtifactManager(tmp_path / "artifacts")
    pipeline = _ExactPipeline(
        artifacts,
        {
            (MusicProviderName.TIDAL, "a1"): DownloadFailureCode.PROVIDER_AUTH,
            (MusicProviderName.TIDAL, "a2"): None,
        },
        source_id,
    )
    executor = Stage25DownloadExecutor(
        database,
        pipeline,
        _Accounts({MusicProviderName.TIDAL: ("a1", "a2")}),
        _Candidates((_candidate(MusicProviderName.TIDAL, "tidal-source"),)),  # type: ignore[arg-type]
        ProviderCandidateRanker(),
    )
    backend = DownloadWorkerBackend(database, pipeline, artifacts, stage25_executor=executor)
    job = await backend.claim("stage25-accounts")
    assert job is not None
    await backend.process(job, "stage25-accounts")

    assert pipeline.calls == [(MusicProviderName.TIDAL, "a1"), (MusicProviderName.TIDAL, "a2")]
    async with database.transaction() as repositories:
        health = await repositories.provider_account_health.get(MusicProviderName.TIDAL, "a1")
        request = await repositories.download_lifecycle.latest_request_for_track(job.track_id)
        assert request is not None
        lifecycle_job = await repositories.download_lifecycle.get_job_for_request(request.id)
        assert lifecycle_job is not None
        attempts = await repositories.provider_resolution.list_attempts(lifecycle_job.id)
    assert health is not None
    assert health.state.value == "AUTH_FAILED"
    assert [item.provider_account_id for item in attempts] == ["a1", "a2"]
