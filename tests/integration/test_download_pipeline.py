from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from app.core.enums import (
    DownloadFailureCode,
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
from app.core.exceptions import DownloadPipelineError
from app.core.models import (
    DownloadPlan,
    NativeMediaInfo,
    PreparedSourceMedia,
    ProviderCapabilities,
    ProviderSourceCheck,
    QualityResolutionResult,
    SourceMediaRequirement,
)
from app.core.quality import QUALITY_OUTPUTS
from app.providers.onthespot.capabilities import ONTHESPOT_CAPABILITIES
from app.services.artifacts import DownloadArtifactManager
from app.services.download_pipeline import DownloadPipeline
from app.services.provider_resolution import ProviderResolver
from app.services.quality_resolution import QualityResolver
from app.services.runtime_prerequisites import TemporaryDiskGuard
from app.storage import Database


class FakeQualityResolver:
    def __init__(self, plans: tuple[DownloadPlan, ...]) -> None:
        self.plans = plans
        self.calls: list[tuple[int, QualityProfile]] = []

    async def resolve(self, track_id: int, profile: QualityProfile) -> QualityResolutionResult:
        self.calls.append((track_id, profile))
        return QualityResolutionResult(
            track_id,
            profile,
            QualityResolutionStatus.RESOLVED
            if self.plans
            else QualityResolutionStatus.NO_AVAILABLE_PROVIDER,
            self.plans,
            (),
            datetime.now(UTC),
        )


class FakeProvider:
    def __init__(self, root: Path, media: dict[MusicProviderName, PreparedSourceMedia]) -> None:
        self.root = root.resolve()
        self.media = media
        self.statuses: dict[MusicProviderName, ProviderRuntimeStatus] = {
            provider: ProviderRuntimeStatus.AVAILABLE for provider in media
        }
        self.checks: list[MusicProviderName] = []
        self.downloads: list[MusicProviderName] = []
        self.cancel_download = False
        self.download_started = asyncio.Event()

    async def check_source(
        self, provider: MusicProviderName, provider_track_id: str
    ) -> ProviderSourceCheck:
        self.checks.append(provider)
        media = self.media[provider]
        return ProviderSourceCheck(
            self.statuses[provider],
            NativeMediaInfo(media.codec, media.container, media.bitrate_kbps),
        )

    def provider_capabilities(self, provider: MusicProviderName) -> ProviderCapabilities:
        return ONTHESPOT_CAPABILITIES[provider]

    async def prepare_source(
        self, provider: MusicProviderName, provider_track_id: str
    ) -> PreparedSourceMedia | None:
        return self.media[provider]

    async def download_source(
        self,
        provider: MusicProviderName,
        provider_track_id: str,
        job_id: str,
        plan_rank: int,
        *,
        timeout_seconds: float,
    ) -> PreparedSourceMedia:
        self.downloads.append(provider)
        self.download_started.set()
        path = self.root / job_id / f"attempt-{plan_rank:03d}" / "source" / "native.bin"
        _write(path, b"native audio")
        if self.cancel_download:
            await asyncio.Future()
        return replace(self.media[provider], file_path=path)


class FakeProbe:
    def __init__(self, media: dict[MusicProviderName, PreparedSourceMedia]) -> None:
        self.media = media

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
        if path.suffix == ".mp3":
            return PreparedSourceMedia(
                provider,
                provider_track_id,
                NativeCodec.MP3,
                NativeContainer.MP3,
                320,
                duration_ms=10_000,
                lossless=False,
                file_path=path,
                native_encoded=native_encoded,
            )
        native = self.media[provider]
        return replace(
            native,
            file_path=path,
            duration_ms=10_000,
            native_encoded=native_encoded,
            upstream_quality_transcoded=upstream_quality_transcoded,
        )


class FakeTranscoder:
    def __init__(self) -> None:
        self.calls = 0
        self.cancel_transcode = False
        self.transcode_started = asyncio.Event()

    async def transcode(
        self, source: Path, partial_output: Path, output: object, metadata: object
    ) -> None:
        self.calls += 1
        _write(partial_output, b"transcoded audio")
        self.transcode_started.set()
        if self.cancel_transcode:
            await asyncio.Future()

    async def tag_copy(self, path: Path, metadata: object) -> bool:
        return True


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _job_directories(path: Path) -> list[Path]:
    return [
        child
        for child in path.iterdir()
        if child.is_dir()
        and len(child.name) == 32
        and all(char in "0123456789abcdef" for char in child.name)
    ]


def _plan(
    provider: MusicProviderName,
    operation: DownloadPlanOperation,
    *,
    source_id: int,
    readiness: DownloadPlanReadiness = DownloadPlanReadiness.CONFIRMED,
) -> DownloadPlan:
    profile = QualityProfile.MP3_320
    requirement = (
        SourceMediaRequirement(required_lossless=True)
        if operation is DownloadPlanOperation.TRANSCODE
        else SourceMediaRequirement(required_codec=NativeCodec.MP3, required_bitrate_kbps=320)
    )
    return DownloadPlan(
        1,
        source_id,
        provider,
        f"{provider.value}-track",
        profile,
        requirement,
        QUALITY_OUTPUTS[profile],
        operation,
        readiness,
        DownloadPlanReason.NATIVE_EXACT_MATCH,
    )


async def _track(database: Database) -> None:
    async with database.transaction() as repositories:
        await repositories.tracks.create_track(
            title="Track", artist="Artist", album="Album", isrc="USRC17607839", duration_ms=10_000
        )


def _pipeline(
    database: Database,
    tmp_path: Path,
    plans: tuple[DownloadPlan, ...],
    provider: FakeProvider,
    disk_guard: TemporaryDiskGuard | None = None,
) -> tuple[DownloadPipeline, DownloadArtifactManager, FakeTranscoder, FakeQualityResolver]:
    resolver = FakeQualityResolver(plans)
    artifacts = DownloadArtifactManager(tmp_path)
    transcoder = FakeTranscoder()
    pipeline = DownloadPipeline(
        database,
        resolver,
        provider,
        artifacts,
        cast(object, FakeProbe(provider.media)),
        cast(object, transcoder),
        disk_guard=disk_guard,
    )
    return pipeline, artifacts, transcoder, resolver


@pytest.mark.asyncio
async def test_low_disk_rejects_before_provider_acquisition(
    database: Database, tmp_path: Path
) -> None:
    await _track(database)
    media = {
        MusicProviderName.DEEZER: PreparedSourceMedia(
            MusicProviderName.DEEZER,
            "deezer-track",
            NativeCodec.MP3,
            NativeContainer.MP3,
            320,
            lossless=False,
        )
    }
    provider = FakeProvider(tmp_path, media)
    disk_guard = TemporaryDiskGuard(
        tmp_path,
        100,
        disk_usage=lambda _: SimpleNamespace(free=99),
    )
    pipeline, artifacts, _, _ = _pipeline(
        database,
        tmp_path,
        (_plan(MusicProviderName.DEEZER, DownloadPlanOperation.DIRECT, source_id=1),),
        provider,
        disk_guard,
    )

    with pytest.raises(DownloadPipelineError) as raised:
        await pipeline.download(1, QualityProfile.MP3_320)

    assert raised.value.code is DownloadFailureCode.TEMP_STORAGE_UNAVAILABLE
    assert provider.checks == []
    assert provider.downloads == []
    assert not any(path.is_dir() and len(path.name) == 32 for path in artifacts.root.iterdir())


@pytest.mark.asyncio
async def test_direct_exact_source_succeeds_and_is_retained_until_release(
    database: Database, tmp_path: Path
) -> None:
    await _track(database)
    media = {
        MusicProviderName.DEEZER: PreparedSourceMedia(
            MusicProviderName.DEEZER,
            "deezer-track",
            NativeCodec.MP3,
            NativeContainer.MP3,
            320,
            lossless=False,
        )
    }
    provider = FakeProvider(tmp_path, media)
    pipeline, artifacts, _, resolver = _pipeline(
        database,
        tmp_path,
        (_plan(MusicProviderName.DEEZER, DownloadPlanOperation.DIRECT, source_id=1),),
        provider,
    )

    result = await pipeline.download(1, QualityProfile.MP3_320)

    assert resolver.calls == [(1, QualityProfile.MP3_320)]
    assert result.file_path.is_file()
    assert result.fallback_index == 0
    artifacts.release(result.job_id)
    artifacts.release(result.job_id)
    assert not result.file_path.exists()


@pytest.mark.asyncio
async def test_auth_change_skips_primary_and_uses_ordered_fallback(
    database: Database, tmp_path: Path
) -> None:
    await _track(database)
    media = {
        provider: PreparedSourceMedia(
            provider,
            f"{provider.value}-track",
            NativeCodec.MP3,
            NativeContainer.MP3,
            320,
            lossless=False,
        )
        for provider in (MusicProviderName.QOBUZ, MusicProviderName.DEEZER)
    }
    provider = FakeProvider(tmp_path, media)
    provider.statuses[MusicProviderName.QOBUZ] = ProviderRuntimeStatus.AUTH_REQUIRED
    plans = (
        _plan(MusicProviderName.QOBUZ, DownloadPlanOperation.DIRECT, source_id=1),
        _plan(MusicProviderName.DEEZER, DownloadPlanOperation.DIRECT, source_id=2),
    )
    pipeline, artifacts, _, _ = _pipeline(database, tmp_path, plans, provider)

    result = await pipeline.download(1, QualityProfile.MP3_320)

    assert provider.checks == [MusicProviderName.QOBUZ, MusicProviderName.DEEZER]
    assert provider.downloads == [MusicProviderName.DEEZER]
    assert result.fallback_index == 1
    assert result.attempts[0].failure_code is DownloadFailureCode.AUTH_REQUIRED
    artifacts.release(result.job_id)


@pytest.mark.asyncio
async def test_lossless_transcode_and_failed_preflight_cleanup(
    database: Database, tmp_path: Path
) -> None:
    await _track(database)
    media = {
        MusicProviderName.TIDAL: PreparedSourceMedia(
            MusicProviderName.TIDAL,
            "tidal-track",
            NativeCodec.AAC,
            NativeContainer.M4A,
            256,
            lossless=False,
        ),
        MusicProviderName.QOBUZ: PreparedSourceMedia(
            MusicProviderName.QOBUZ,
            "qobuz-track",
            NativeCodec.FLAC,
            NativeContainer.FLAC,
            lossless=True,
        ),
    }
    provider = FakeProvider(tmp_path, media)
    plans = (
        _plan(
            MusicProviderName.TIDAL,
            DownloadPlanOperation.TRANSCODE,
            source_id=1,
            readiness=DownloadPlanReadiness.REQUIRES_PREFLIGHT,
        ),
        _plan(MusicProviderName.QOBUZ, DownloadPlanOperation.TRANSCODE, source_id=2),
    )
    pipeline, artifacts, transcoder, _ = _pipeline(database, tmp_path, plans, provider)

    result = await pipeline.download(1, QualityProfile.MP3_320)

    assert provider.downloads == [MusicProviderName.QOBUZ]
    assert transcoder.calls == 1
    assert not (tmp_path / result.job_id / "attempt-001").exists()
    assert result.transcoded is True
    artifacts.release(result.job_id)


@pytest.mark.asyncio
async def test_all_plans_becoming_auth_required_is_typed_and_clean(
    database: Database, tmp_path: Path
) -> None:
    await _track(database)
    media = {
        MusicProviderName.QOBUZ: PreparedSourceMedia(
            MusicProviderName.QOBUZ,
            "qobuz-track",
            NativeCodec.FLAC,
            NativeContainer.FLAC,
            lossless=True,
        )
    }
    provider = FakeProvider(tmp_path, media)
    provider.statuses[MusicProviderName.QOBUZ] = ProviderRuntimeStatus.AUTH_REQUIRED
    pipeline, _, _, _ = _pipeline(
        database,
        tmp_path,
        (_plan(MusicProviderName.QOBUZ, DownloadPlanOperation.TRANSCODE, source_id=1),),
        provider,
    )

    with pytest.raises(DownloadPipelineError) as captured:
        await pipeline.download(1, QualityProfile.MP3_320)

    assert captured.value.code is DownloadFailureCode.NO_AVAILABLE_PROVIDER
    assert _job_directories(tmp_path) == []


@pytest.mark.asyncio
async def test_cancellation_propagates_and_removes_partial_job(
    database: Database, tmp_path: Path
) -> None:
    await _track(database)
    media = {
        MusicProviderName.DEEZER: PreparedSourceMedia(
            MusicProviderName.DEEZER,
            "deezer-track",
            NativeCodec.MP3,
            NativeContainer.MP3,
            320,
            lossless=False,
        )
    }
    provider = FakeProvider(tmp_path, media)
    provider.cancel_download = True
    pipeline, _, _, _ = _pipeline(
        database,
        tmp_path,
        (_plan(MusicProviderName.DEEZER, DownloadPlanOperation.DIRECT, source_id=1),),
        provider,
    )
    task = asyncio.create_task(pipeline.download(1, QualityProfile.MP3_320))
    await provider.download_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert _job_directories(tmp_path) == []


@pytest.mark.asyncio
async def test_transcode_cancellation_propagates_and_removes_partial_job(
    database: Database, tmp_path: Path
) -> None:
    await _track(database)
    media = {
        MusicProviderName.QOBUZ: PreparedSourceMedia(
            MusicProviderName.QOBUZ,
            "qobuz-track",
            NativeCodec.FLAC,
            NativeContainer.FLAC,
            lossless=True,
        )
    }
    provider = FakeProvider(tmp_path, media)
    pipeline, _, transcoder, _ = _pipeline(
        database,
        tmp_path,
        (_plan(MusicProviderName.QOBUZ, DownloadPlanOperation.TRANSCODE, source_id=1),),
        provider,
    )
    transcoder.cancel_transcode = True
    task = asyncio.create_task(pipeline.download(1, QualityProfile.MP3_320))
    await transcoder.transcode_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert _job_directories(tmp_path) == []


@pytest.mark.asyncio
async def test_persisted_track_source_through_real_stage_4_and_5_to_stage_6(
    database: Database, tmp_path: Path
) -> None:
    await _track(database)
    async with database.transaction() as repositories:
        await repositories.track_sources.upsert_source(
            track_id=1,
            provider=MusicProviderName.DEEZER,
            provider_track_id="deezer-track",
            url=None,
        )
    media = {
        MusicProviderName.DEEZER: PreparedSourceMedia(
            MusicProviderName.DEEZER,
            "deezer-track",
            NativeCodec.MP3,
            NativeContainer.MP3,
            320,
            lossless=False,
        )
    }
    provider = FakeProvider(tmp_path, media)
    artifacts = DownloadArtifactManager(tmp_path)
    pipeline = DownloadPipeline(
        database,
        QualityResolver(ProviderResolver(database, provider)),
        provider,
        artifacts,
        cast(object, FakeProbe(media)),
        cast(object, FakeTranscoder()),
    )

    result = await pipeline.download(1, QualityProfile.MP3_320)

    assert result.provider is MusicProviderName.DEEZER
    assert result.operation is DownloadPlanOperation.DIRECT
    assert result.file_path.is_file()
    artifacts.release(result.job_id)
