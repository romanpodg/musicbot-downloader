from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update

from app.config import Settings
from app.core.enums import (
    DownloadFailureCode,
    DownloadPlanOperation,
    DownloadPlanReadiness,
    MusicProviderName,
    QualityProfile,
    QueueErrorCode,
    QueueJobStatus,
)
from app.core.exceptions import (
    DownloadPipelineError,
    QueueFullError,
    TrackNotFound,
    UploadRetryableError,
    UploadTerminalError,
    WorkerLimitError,
)
from app.core.models import DownloadResult, PreparedSourceMedia, UploadRequest, UploadResult
from app.services.artifacts import DownloadArtifactManager
from app.services.queues import DownloadQueueService, UploadQueueService, WorkerSettingsService
from app.services.workers import (
    AsyncWorkerPool,
    DownloadWorkerBackend,
    UploadWorkerBackend,
)
from app.storage import Database
from app.storage.models import UploadJob
from app.storage.repositories.queue import DownloadJobRepository


@dataclass
class ManualClock:
    now: datetime = datetime(2026, 8, 18, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class ScriptedPipeline:
    def __init__(self, artifacts: DownloadArtifactManager, outcomes: list[object]) -> None:
        self.artifacts = artifacts
        self.outcomes = outcomes
        self.calls: list[tuple[int, QualityProfile]] = []

    async def download(self, track_id: int, quality_profile: QualityProfile) -> DownloadResult:
        self.calls.append((track_id, quality_profile))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        job_id, _ = self.artifacts.create_job()
        path = self.artifacts.final_path(job_id, "mp3")
        path.write_bytes(b"audio")
        media = PreparedSourceMedia(
            MusicProviderName.QOBUZ,
            "provider-id",
            file_path=path,
        )
        return DownloadResult(
            job_id,
            track_id,
            quality_profile,
            1,
            MusicProviderName.QOBUZ,
            "provider-id",
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


class ScriptedUploader:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.requests: list[UploadRequest] = []

    async def upload(self, request: UploadRequest) -> UploadResult:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return UploadResult(external_id="delivered")


async def _track(database: Database, title: str = "Song") -> int:
    async with database.transaction() as repositories:
        track = await repositories.tracks.create_track(title=title, artist="Artist")
        return track.id


async def test_submission_persists_capacity_and_no_singleflight(database: Database) -> None:
    track_id = await _track(database)
    queue = DownloadQueueService(database, max_size=2)
    first = await queue.submit(track_id=track_id, quality_profile=QualityProfile.MP3_320)
    second = await queue.submit(track_id=track_id, quality_profile=QualityProfile.MP3_320)

    assert first.id != second.id
    assert first.status is QueueJobStatus.QUEUED
    assert (await queue.get_download_job(first.id)).attempt_count == 0
    with pytest.raises(QueueFullError):
        await queue.submit(track_id=track_id, quality_profile=QualityProfile.MP3_128)
    with pytest.raises(TrackNotFound):
        await DownloadQueueService(database, max_size=3).submit(
            track_id=9999, quality_profile=QualityProfile.MP3_128
        )
    assert await queue.cancel(first.id) is QueueJobStatus.CANCELLED
    assert (await queue.get_download_job(first.id)).status is QueueJobStatus.CANCELLED


async def test_concurrent_claim_is_unique_and_fifo(database: Database) -> None:
    track_ids = [await _track(database, str(index)) for index in range(3)]
    queue = DownloadQueueService(database, max_size=4)
    jobs = [
        await queue.submit(track_id=track_id, quality_profile=QualityProfile.MP3_128)
        for track_id in track_ids
    ]

    async def claim(worker: int) -> int | None:
        now = datetime.now(UTC)
        async with database.transaction() as repositories:
            job = await repositories.download_jobs.claim(
                worker_id=f"download-{worker}",
                now=now,
                lease_expires_at=now + timedelta(minutes=15),
            )
            return job.id if job is not None else None

    sequential = [await claim(index) for index in range(3)]
    assert sequential == [job.id for job in jobs]

    fourth_track = await _track(database, "fourth")
    fourth = await queue.submit(track_id=fourth_track, quality_profile=QualityProfile.MP3_128)
    concurrent = await asyncio.gather(*(claim(index + 3) for index in range(5)))
    assert [job_id for job_id in concurrent if job_id is not None] == [fourth.id]


async def test_expired_lease_is_reclaimed_but_valid_lease_is_not(database: Database) -> None:
    clock = ManualClock()
    track_id = await _track(database)
    queue = DownloadQueueService(database, max_size=1, clock=clock)
    submitted = await queue.submit(track_id=track_id, quality_profile=QualityProfile.LOSSLESS)
    async with database.transaction() as repositories:
        first = await repositories.download_jobs.claim(
            worker_id="download-1",
            now=clock(),
            lease_expires_at=clock() + timedelta(seconds=10),
        )
    assert first is not None
    async with database.transaction() as repositories:
        await repositories.download_jobs.recover_expired(clock(), 3)
        assert (
            await repositories.download_jobs.claim(
                worker_id="download-2",
                now=clock(),
                lease_expires_at=clock() + timedelta(seconds=10),
            )
            is None
        )
    clock.advance(11)
    async with database.transaction() as repositories:
        await repositories.download_jobs.recover_expired(clock(), 3)
        second = await repositories.download_jobs.claim(
            worker_id="download-2",
            now=clock(),
            lease_expires_at=clock() + timedelta(seconds=10),
        )
    assert second is not None and second.id == submitted.id and second.attempt_count == 2


async def test_download_handoff_and_upload_success_release_artifact(
    database: Database, tmp_path: Path
) -> None:
    clock = ManualClock()
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    pipeline = ScriptedPipeline(artifacts, [object()])
    track_id = await _track(database)
    download_queue = DownloadQueueService(database, max_size=1, clock=clock)
    upload_queue = UploadQueueService(database, artifacts, clock=clock)
    submitted = await download_queue.submit(
        track_id=track_id, quality_profile=QualityProfile.MP3_320
    )
    downloader = DownloadWorkerBackend(database, pipeline, artifacts, clock=clock)
    claimed = await downloader.claim("download-1")
    assert claimed is not None
    await downloader.process(claimed, "download-1")

    completed = await download_queue.get_download_job(submitted.id)
    uploads = await upload_queue.list_upload_jobs()
    assert completed.status is QueueJobStatus.SUCCEEDED
    assert len(uploads) == 1
    async with database.transaction() as repositories:
        duplicate = await repositories.download_jobs.handoff(
            job_id=submitted.id,
            worker_id="download-1",
            artifact_job_id="0" * 32,
            artifact_path="unused",
            now=clock(),
        )
    assert duplicate is None
    async with database.transaction() as repositories:
        persisted = await repositories.upload_jobs.get(uploads[0].id)
        assert persisted is not None
        artifact_root = artifacts.root / persisted.artifact_job_id
    assert artifact_root.exists()

    uploader = ScriptedUploader([object()])
    upload_worker = UploadWorkerBackend(database, uploader, upload_queue, clock=clock)
    upload = await upload_worker.claim("upload-1")
    assert upload is not None
    await upload_worker.process(upload, "upload-1")
    assert (await upload_queue.get_upload_job(upload.id)).status is QueueJobStatus.SUCCEEDED
    assert len(uploader.requests) == 1
    assert not artifact_root.exists()


async def test_download_retry_invokes_stage6_fresh(database: Database, tmp_path: Path) -> None:
    clock = ManualClock()
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    pipeline = ScriptedPipeline(
        artifacts,
        [DownloadPipelineError(DownloadFailureCode.NO_AVAILABLE_PROVIDER), object()],
    )
    track_id = await _track(database)
    queue = DownloadQueueService(database, max_size=1, clock=clock)
    submitted = await queue.submit(track_id=track_id, quality_profile=QualityProfile.MP3_128)
    backend = DownloadWorkerBackend(database, pipeline, artifacts, clock=clock)

    first = await backend.claim("download-1")
    assert first is not None
    await backend.process(first, "download-1")
    retry = await queue.get_download_job(submitted.id)
    assert retry.status is QueueJobStatus.QUEUED
    assert retry.last_error_code == DownloadFailureCode.NO_AVAILABLE_PROVIDER.value
    assert await backend.claim("download-1") is None
    clock.advance(2)
    second = await backend.claim("download-1")
    assert second is not None
    await backend.process(second, "download-1")
    assert len(pipeline.calls) == 2
    assert (await queue.get_download_job(submitted.id)).status is QueueJobStatus.SUCCEEDED


async def test_temporary_storage_failure_uses_bounded_download_backoff(
    database: Database, tmp_path: Path
) -> None:
    clock = ManualClock()
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    pipeline = ScriptedPipeline(
        artifacts,
        [DownloadPipelineError(DownloadFailureCode.TEMP_STORAGE_UNAVAILABLE)],
    )
    track_id = await _track(database)
    queue = DownloadQueueService(database, max_size=1, clock=clock)
    submitted = await queue.submit(track_id=track_id, quality_profile=QualityProfile.MP3_128)
    backend = DownloadWorkerBackend(database, pipeline, artifacts, clock=clock)

    claimed = await backend.claim("download-1")
    assert claimed is not None
    await backend.process(claimed, "download-1")

    retry = await queue.get_download_job(submitted.id)
    assert retry.status is QueueJobStatus.QUEUED
    assert retry.last_error_code == DownloadFailureCode.TEMP_STORAGE_UNAVAILABLE.value
    assert await backend.claim("download-1") is None


async def test_upload_retry_preserves_then_releases(database: Database, tmp_path: Path) -> None:
    clock = ManualClock()
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    pipeline = ScriptedPipeline(artifacts, [object()])
    track_id = await _track(database)
    download_queue = DownloadQueueService(database, max_size=1, clock=clock)
    upload_queue = UploadQueueService(database, artifacts, clock=clock)
    await download_queue.submit(track_id=track_id, quality_profile=QualityProfile.AAC_256)
    downloader = DownloadWorkerBackend(database, pipeline, artifacts, clock=clock)
    download = await downloader.claim("download-1")
    assert download is not None
    await downloader.process(download, "download-1")
    uploader = ScriptedUploader([UploadRetryableError(), object()])
    backend = UploadWorkerBackend(database, uploader, upload_queue, clock=clock)

    first = await backend.claim("upload-1")
    assert first is not None
    artifact_root = artifacts.root / first.artifact_job_id
    await backend.process(first, "upload-1")
    retried = await upload_queue.get_upload_job(first.id)
    assert retried.status is QueueJobStatus.QUEUED and retried.attempt_count == 1
    assert artifact_root.exists()
    clock.advance(2)
    second = await backend.claim("upload-1")
    assert second is not None
    await backend.process(second, "upload-1")
    assert (await upload_queue.get_upload_job(first.id)).status is QueueJobStatus.SUCCEEDED
    assert not artifact_root.exists()


async def test_upload_terminal_failure_releases_artifact(
    database: Database, tmp_path: Path
) -> None:
    clock = ManualClock()
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    pipeline = ScriptedPipeline(artifacts, [object()])
    track_id = await _track(database)
    downloads = DownloadQueueService(database, max_size=1, clock=clock)
    uploads = UploadQueueService(database, artifacts, clock=clock)
    await downloads.submit(track_id=track_id, quality_profile=QualityProfile.MP3_128)
    download_backend = DownloadWorkerBackend(database, pipeline, artifacts, clock=clock)
    download = await download_backend.claim("download-1")
    assert download is not None
    await download_backend.process(download, "download-1")
    uploader = ScriptedUploader([UploadTerminalError()])
    backend = UploadWorkerBackend(database, uploader, uploads, clock=clock)
    upload = await backend.claim("upload-1")
    assert upload is not None
    artifact_root = artifacts.root / upload.artifact_job_id
    await backend.process(upload, "upload-1")
    assert (await uploads.get_upload_job(upload.id)).status is QueueJobStatus.FAILED
    assert not artifact_root.exists()


class BlockingPipeline:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def download(self, track_id: int, quality_profile: QualityProfile) -> DownloadResult:
        del track_id, quality_profile
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        raise AssertionError("unreachable")


async def test_running_download_cancellation_is_cooperative(
    database: Database, tmp_path: Path
) -> None:
    clock = ManualClock()
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    pipeline = BlockingPipeline()
    track_id = await _track(database)
    queue = DownloadQueueService(database, max_size=1, clock=clock)
    submitted = await queue.submit(track_id=track_id, quality_profile=QualityProfile.MP3_128)
    backend = DownloadWorkerBackend(database, pipeline, artifacts, clock=clock)
    claimed = await backend.claim("download-1")
    assert claimed is not None
    operation = asyncio.create_task(backend.process(claimed, "download-1"))
    await pipeline.started.wait()
    assert await queue.cancel(submitted.id) is QueueJobStatus.RUNNING
    operation.cancel()
    await operation
    assert pipeline.cancelled.is_set()
    assert (await queue.get_download_job(submitted.id)).status is QueueJobStatus.CANCELLED
    assert await UploadQueueService(database, artifacts).list_upload_jobs() == ()


async def test_handoff_failure_releases_and_does_not_succeed(
    database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = ManualClock()
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    pipeline = ScriptedPipeline(artifacts, [object()])
    track_id = await _track(database)
    queue = DownloadQueueService(database, max_size=1, clock=clock)
    submitted = await queue.submit(track_id=track_id, quality_profile=QualityProfile.MP3_128)
    backend = DownloadWorkerBackend(database, pipeline, artifacts, clock=clock)
    claimed = await backend.claim("download-1")
    assert claimed is not None

    async def fail_handoff(*args: object, **kwargs: object) -> None:
        raise RuntimeError("controlled persistence failure")

    monkeypatch.setattr(DownloadJobRepository, "handoff", fail_handoff)
    await backend.process(claimed, "download-1")
    job = await queue.get_download_job(submitted.id)
    assert job.status is QueueJobStatus.QUEUED
    assert job.last_error_code == QueueErrorCode.DOWNLOAD_PERSISTENCE_ERROR.value
    assert list(artifacts.root.glob("*")) == []


async def test_tampered_upload_path_is_never_read_or_deleted(
    database: Database, tmp_path: Path
) -> None:
    clock = ManualClock()
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    pipeline = ScriptedPipeline(artifacts, [object()])
    track_id = await _track(database)
    downloads = DownloadQueueService(database, max_size=1, clock=clock)
    uploads = UploadQueueService(database, artifacts, clock=clock)
    await downloads.submit(track_id=track_id, quality_profile=QualityProfile.MP3_128)
    download_backend = DownloadWorkerBackend(database, pipeline, artifacts, clock=clock)
    download = await download_backend.claim("download-1")
    assert download is not None
    await download_backend.process(download, "download-1")
    upload = (await uploads.list_upload_jobs())[0]
    external = tmp_path / "outside.mp3"
    external.write_bytes(b"private")
    async with database.engine.begin() as connection:
        await connection.execute(
            update(UploadJob).where(UploadJob.id == upload.id).values(artifact_path=str(external))
        )
    executor = ScriptedUploader([object()])
    backend = UploadWorkerBackend(database, executor, uploads, clock=clock)
    claimed = await backend.claim("upload-1")
    assert claimed is not None
    await backend.process(claimed, "upload-1")
    assert executor.requests == []
    assert external.read_bytes() == b"private"
    failed = await uploads.get_upload_job(upload.id)
    assert failed.status is QueueJobStatus.FAILED
    assert failed.last_error_code == QueueErrorCode.UPLOAD_ARTIFACT_INVALID.value


async def test_runtime_settings_persist_clamp_and_reject_max(database: Database) -> None:
    initial = Settings(_env_file=None, download_workers_default=2, download_workers_max=8)
    service = WorkerSettingsService(database, initial)
    assert (await service.initialize()).download.current == 2
    await service.set_download_workers(7)
    assert (await WorkerSettingsService(database, initial).get_values()).download.current == 7

    lowered = Settings(
        _env_file=None,
        download_workers_default=2,
        download_workers_max=4,
    )
    recreated = WorkerSettingsService(database, lowered)
    assert (await recreated.initialize()).download.current == 4
    assert (await WorkerSettingsService(database, lowered).get_values()).download.current == 4
    with pytest.raises(WorkerLimitError):
        await recreated.set_download_workers(5)
    with pytest.raises(WorkerLimitError):
        await recreated.set_upload_workers(lowered.upload_workers_max + 1)


class BlockingBackend:
    def __init__(self, count: int) -> None:
        self.wake_event = asyncio.Event()
        self.jobs = [_PoolJob(index) for index in range(count)]
        self.started = 0
        self.release = asyncio.Event()

    async def claim(self, worker_id: str) -> _PoolJob | None:
        del worker_id
        return self.jobs.pop(0) if self.jobs else None

    async def process(self, job: object, worker_id: str) -> None:
        del job, worker_id
        self.started += 1
        await self.release.wait()

    async def heartbeat(self, job_id: int, worker_id: str) -> bool:
        del job_id, worker_id
        return True


@dataclass
class _PoolJob:
    id: int


async def _wait_until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(2):
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


async def test_pool_upscale_and_graceful_downscale() -> None:
    backend = BlockingBackend(5)
    pool = AsyncWorkerPool("download", backend, poll_seconds=0.01, heartbeat_seconds=1)
    await pool.start(2)
    await _wait_until(lambda: backend.started == 2)
    await pool.resize(5)
    await _wait_until(lambda: backend.started == 5)
    assert pool.actual == 5
    await pool.resize(2)
    assert pool.actual == 5
    backend.release.set()
    await _wait_until(lambda: pool.actual == 2)
    assert pool.desired == 2
    await pool.stop(1)
