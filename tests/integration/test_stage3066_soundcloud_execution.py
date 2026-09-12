"""Stage 30.6.6 execution proofs through the existing Stage 25 pipeline."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.enums import (
    DownloadAttemptStatus,
    DownloadPlanOperation,
    DownloadPlanReadiness,
    DownloadPlanReason,
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    ProviderRuntimeStatus,
    QualityProfile,
    QualityResolutionStatus,
)
from app.core.models import (
    DownloadPlan,
    PreparedSourceMedia,
    ProviderSourceCheck,
    QualityResolutionResult,
    SourceMediaRequirement,
)
from app.core.quality import QUALITY_OUTPUTS
from app.services.artifacts import DownloadArtifactManager
from app.services.download_pipeline import DownloadPipeline
from app.storage import Database


class _Resolver:
    def __init__(self, plans: tuple[DownloadPlan, ...]) -> None:
        self._plans = plans

    async def resolve(
        self, track_id: int, quality_profile: QualityProfile
    ) -> QualityResolutionResult:
        return QualityResolutionResult(
            track_id,
            quality_profile,
            QualityResolutionStatus.RESOLVED,
            self._plans,
            (),
            datetime.now(UTC),
        )


class _Provider:
    def __init__(self, root: Path, *, unavailable: set[MusicProviderName] | None = None) -> None:
        self._root = root
        self._unavailable = unavailable or set()
        self.prepared: list[MusicProviderName] = []
        self.downloaded: list[MusicProviderName] = []

    async def check_source(
        self, provider: MusicProviderName, provider_track_id: str
    ) -> ProviderSourceCheck:
        if provider in self._unavailable:
            return ProviderSourceCheck(ProviderRuntimeStatus.SOURCE_UNAVAILABLE)
        return ProviderSourceCheck(ProviderRuntimeStatus.AVAILABLE)

    async def prepare_source(
        self, provider: MusicProviderName, provider_track_id: str
    ) -> PreparedSourceMedia | None:
        self.prepared.append(provider)
        return PreparedSourceMedia(
            provider,
            provider_track_id,
            NativeCodec.MP3,
            NativeContainer.MP3,
            128,
            lossless=False,
        )

    async def download_source(
        self,
        provider: MusicProviderName,
        provider_track_id: str,
        job_id: str,
        plan_rank: int,
        *,
        timeout_seconds: float,
        account_id: str | None = None,
    ) -> PreparedSourceMedia:
        assert account_id is None
        self.downloaded.append(provider)
        path = self._root / job_id / f"attempt-{plan_rank:03d}" / "source" / "native.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"native")
        return PreparedSourceMedia(
            provider,
            provider_track_id,
            NativeCodec.MP3,
            NativeContainer.MP3,
            128,
            lossless=False,
            file_path=path,
        )


class _Probe:
    def __init__(self, *, drift_soundcloud: bool) -> None:
        self._drift_soundcloud = drift_soundcloud

    async def probe(
        self,
        path: Path,
        *,
        provider: MusicProviderName,
        provider_track_id: str,
        native_encoded: bool,
        provider_decrypted: bool = False,
        upstream_quality_transcoded: bool = False,
    ) -> PreparedSourceMedia:
        if provider is MusicProviderName.SOUNDCLOUD and self._drift_soundcloud:
            # The declared public result is MP3_128, but the actual acquired
            # file drifts.  The pipeline must reject it before cache/delivery.
            return PreparedSourceMedia(
                provider,
                provider_track_id,
                NativeCodec.AAC,
                NativeContainer.M4A,
                128,
                lossless=False,
                file_path=path,
                native_encoded=native_encoded,
            )
        return PreparedSourceMedia(
            provider,
            provider_track_id,
            NativeCodec.MP3,
            NativeContainer.MP3,
            128,
            lossless=False,
            file_path=path,
            native_encoded=native_encoded,
        )


class _Transcoder:
    async def tag_copy(self, path: Path, metadata: dict[str, str]) -> bool:
        return True


def _plan(
    provider: MusicProviderName, track_source_id: int, readiness: DownloadPlanReadiness
) -> DownloadPlan:
    return DownloadPlan(
        track_id=1,
        track_source_id=track_source_id,
        provider=provider,
        provider_track_id=(
            "https://soundcloud.com/artist/public-track"
            if provider is MusicProviderName.SOUNDCLOUD
            else "https://artist.bandcamp.com/track/fallback"
        ),
        requested_profile=QualityProfile.MP3_128,
        source_expectation=SourceMediaRequirement(
            required_codec=NativeCodec.MP3,
            required_bitrate_kbps=128,
        ),
        output_specification=QUALITY_OUTPUTS[QualityProfile.MP3_128],
        operation=DownloadPlanOperation.DIRECT,
        readiness=readiness,
        reason=(
            DownloadPlanReason.PROVIDER_PREFLIGHT_REQUIRED
            if readiness is DownloadPlanReadiness.REQUIRES_PREFLIGHT
            else DownloadPlanReason.NATIVE_EXACT_MATCH
        ),
    )


@pytest.mark.asyncio
async def test_public_soundcloud_mp3_128_executes_through_the_existing_pipeline(
    database: Database, tmp_path: Path
) -> None:
    async with database.transaction() as repositories:
        await repositories.tracks.create_track(title="Public SoundCloud", artist="Stage 30.6.6")

    artifacts = DownloadArtifactManager(tmp_path)
    provider = _Provider(tmp_path)
    pipeline = DownloadPipeline(
        database,
        _Resolver(
            (_plan(MusicProviderName.SOUNDCLOUD, 1, DownloadPlanReadiness.REQUIRES_PREFLIGHT),)
        ),
        provider,
        artifacts,
        _Probe(drift_soundcloud=False),  # type: ignore[arg-type]
        _Transcoder(),  # type: ignore[arg-type]
    )

    result = await pipeline.download(1, QualityProfile.MP3_128)

    assert provider.prepared == [MusicProviderName.SOUNDCLOUD]
    assert provider.downloaded == [MusicProviderName.SOUNDCLOUD]
    assert result.provider is MusicProviderName.SOUNDCLOUD
    assert result.output_media.codec is NativeCodec.MP3
    assert result.output_media.container is NativeContainer.MP3
    assert result.output_media.bitrate_kbps == 128
    assert [attempt.status for attempt in result.attempts] == [DownloadAttemptStatus.SUCCEEDED]
    artifacts.release(result.job_id)


@pytest.mark.asyncio
async def test_unexpected_soundcloud_media_is_rejected_before_successful_artifact_handoff(
    database: Database, tmp_path: Path
) -> None:
    async with database.transaction() as repositories:
        await repositories.tracks.create_track(title="SoundCloud drift", artist="Stage 30.6.6")

    artifacts = DownloadArtifactManager(tmp_path)
    provider = _Provider(tmp_path)
    pipeline = DownloadPipeline(
        database,
        _Resolver(
            (
                _plan(MusicProviderName.SOUNDCLOUD, 1, DownloadPlanReadiness.REQUIRES_PREFLIGHT),
                _plan(MusicProviderName.BANDCAMP, 2, DownloadPlanReadiness.CONFIRMED),
            )
        ),
        provider,
        artifacts,
        _Probe(drift_soundcloud=True),  # type: ignore[arg-type]
        _Transcoder(),  # type: ignore[arg-type]
    )

    result = await pipeline.download(1, QualityProfile.MP3_128)

    assert provider.prepared == [MusicProviderName.SOUNDCLOUD]
    assert provider.downloaded == [MusicProviderName.SOUNDCLOUD, MusicProviderName.BANDCAMP]
    assert result.provider is MusicProviderName.BANDCAMP
    assert [attempt.status for attempt in result.attempts] == [
        DownloadAttemptStatus.FAILED,
        DownloadAttemptStatus.SUCCEEDED,
    ]
    assert result.file_path.is_file()
    assert not await asyncio.to_thread(
        lambda: list(tmp_path.glob("*/attempt-001/source/native.bin"))
    )
    artifacts.release(result.job_id)


@pytest.mark.asyncio
async def test_unavailable_soundcloud_source_uses_normal_stage25_fallback(
    database: Database, tmp_path: Path
) -> None:
    async with database.transaction() as repositories:
        await repositories.tracks.create_track(
            title="Unavailable SoundCloud", artist="Stage 30.6.6"
        )

    artifacts = DownloadArtifactManager(tmp_path)
    provider = _Provider(tmp_path, unavailable={MusicProviderName.SOUNDCLOUD})
    pipeline = DownloadPipeline(
        database,
        _Resolver(
            (
                _plan(MusicProviderName.SOUNDCLOUD, 1, DownloadPlanReadiness.REQUIRES_PREFLIGHT),
                _plan(MusicProviderName.BANDCAMP, 2, DownloadPlanReadiness.CONFIRMED),
            )
        ),
        provider,
        artifacts,
        _Probe(drift_soundcloud=False),  # type: ignore[arg-type]
        _Transcoder(),  # type: ignore[arg-type]
    )

    result = await pipeline.download(1, QualityProfile.MP3_128)

    assert provider.prepared == []
    assert provider.downloaded == [MusicProviderName.BANDCAMP]
    assert result.provider is MusicProviderName.BANDCAMP
    assert [attempt.status for attempt in result.attempts] == [
        DownloadAttemptStatus.SKIPPED,
        DownloadAttemptStatus.SUCCEEDED,
    ]
    artifacts.release(result.job_id)
