from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from app.core.delivery_targets import PrivateUserTarget
from app.core.enums import (
    DownloadFailureCode,
    DownloadPlanOperation,
    DownloadPlanReadiness,
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    QualityProfile,
)
from app.core.exceptions import DownloadPipelineError
from app.core.models import DownloadResult, PreparedSourceMedia, ProviderMediaCapabilities
from app.core.provider_resolution import (
    CanonicalMediaIdentity,
    ProviderCandidate,
    ProviderCandidateRanker,
    match_media,
)
from app.services.artifacts import DownloadArtifactManager
from app.services.stage25_execution import Stage25DownloadExecutor
from app.services.workers import DownloadWorkerBackend
from app.storage import Database
from app.storage.models.base import utc_now


@dataclass
class _Candidates:
    values: tuple[ProviderCandidate, ...]

    async def resolve(self, identity, **kwargs):  # type: ignore[no-untyped-def]
        return self.values


@dataclass
class _Accounts:
    values: dict[MusicProviderName, tuple[str, ...]]

    async def list_provider_accounts(self, provider: MusicProviderName) -> tuple[str, ...]:
        return self.values.get(provider, ())


class _ExactPipeline:
    def __init__(
        self,
        artifacts: DownloadArtifactManager,
        outcomes: dict[tuple[MusicProviderName, str | None], DownloadFailureCode | None],
        source_id: int,
    ) -> None:
        self._artifacts = artifacts
        self._outcomes = outcomes
        self._source_id = source_id
        self.calls: list[tuple[MusicProviderName, str | None]] = []

    async def download_selected(
        self,
        track_id: int,
        quality_profile: QualityProfile,
        *,
        provider: MusicProviderName,
        provider_media_id: str,
        account_id: str | None = None,
    ) -> DownloadResult:
        self.calls.append((provider, account_id))
        failure = self._outcomes[(provider, account_id)]
        if failure is not None:
            raise DownloadPipelineError(failure)
        artifact_job_id, _ = self._artifacts.create_job()
        path = self._artifacts.final_path(artifact_job_id, "mp3")
        path.write_bytes(b"stage25-audio")
        media = PreparedSourceMedia(
            provider,
            provider_media_id,
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
            self._source_id,
            provider,
            provider_media_id,
            DownloadPlanOperation.DIRECT,
            DownloadPlanReadiness.CONFIRMED,
            media,
            media,
            path,
            path.stat().st_size,
            False,
            0,
            (),
            datetime.now(UTC),
        )


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


def _candidate(provider: MusicProviderName, media_id: str) -> ProviderCandidate:
    identity = CanonicalMediaIdentity.from_values(
        title="Stage 25", artist="Executor", isrc="USABC1234567"
    )
    return ProviderCandidate(
        provider,
        media_id,
        identity,
        match_media(identity, identity),
        ProviderMediaCapabilities(supports_lossy=True),
    )


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
