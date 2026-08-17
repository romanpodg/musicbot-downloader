from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from app.core.enums import (
    DownloadFailureCode,
    DownloadPlanOperation,
    DownloadPlanReadiness,
    MusicProviderName,
    QualityProfile,
    QueueErrorCode,
    QueueJobStatus,
    SubscriberStatus,
)
from app.core.exceptions import (
    DownloadPipelineError,
    InvalidRequestKeyError,
    QueueFullError,
    UploadRetryableError,
)
from app.core.models import DownloadResult, PreparedSourceMedia, UploadRequest, UploadResult
from app.services.artifacts import DownloadArtifactManager
from app.services.queues import DownloadQueueService, UploadQueueService
from app.services.singleflight import SingleFlightService, SubscriberNotifier
from app.services.workers import DownloadWorkerBackend, UploadWorkerBackend
from app.storage import Database
from app.storage.models import DownloadFlight, DownloadJob, JobSubscriber, UploadJob


@dataclass
class ManualClock:
    now: datetime = datetime(2026, 8, 18, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class RecordingPipeline:
    def __init__(self, artifacts: DownloadArtifactManager) -> None:
        self.artifacts = artifacts
        self.calls: list[tuple[int, QualityProfile]] = []

    async def download(self, track_id: int, quality_profile: QualityProfile) -> DownloadResult:
        self.calls.append((track_id, quality_profile))
        artifact_id, _ = self.artifacts.create_job()
        path = self.artifacts.final_path(artifact_id, "mp3")
        path.write_bytes(b"shared audio")
        media = PreparedSourceMedia(MusicProviderName.QOBUZ, "source", file_path=path)
        return DownloadResult(
            artifact_id,
            track_id,
            quality_profile,
            1,
            MusicProviderName.QOBUZ,
            "source",
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


class RecordingUploader:
    def __init__(self) -> None:
        self.requests: list[UploadRequest] = []

    async def upload(self, request: UploadRequest) -> UploadResult:
        self.requests.append(request)
        return UploadResult(external_id="shared-result")


class ScriptedUploader(RecordingUploader):
    def __init__(self, outcomes: list[Exception | None]) -> None:
        super().__init__()
        self.outcomes = outcomes

    async def upload(self, request: UploadRequest) -> UploadResult:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if outcome is not None:
            raise outcome
        return UploadResult(external_id="shared-result")


class FailingPipeline:
    def __init__(self) -> None:
        self.calls = 0

    async def download(self, track_id: int, quality_profile: QualityProfile) -> DownloadResult:
        del track_id, quality_profile
        self.calls += 1
        raise DownloadPipelineError(DownloadFailureCode.OUTPUT_VALIDATION_FAILED)


class RetryingPipeline(RecordingPipeline):
    async def download(self, track_id: int, quality_profile: QualityProfile) -> DownloadResult:
        if not self.calls:
            self.calls.append((track_id, quality_profile))
            raise DownloadPipelineError(DownloadFailureCode.NO_AVAILABLE_PROVIDER)
        return await super().download(track_id, quality_profile)


class RecordingCanceller:
    def __init__(self) -> None:
        self.downloads: list[int] = []
        self.uploads: list[int] = []

    async def cancel_download_operation(self, job_id: int) -> None:
        self.downloads.append(job_id)

    async def cancel_upload_operation(self, job_id: int) -> None:
        self.uploads.append(job_id)


async def _track(database: Database, title: str = "Song") -> int:
    async with database.transaction() as repositories:
        track = await repositories.tracks.create_track(title=title, artist="Artist")
        return track.id


async def _counts(database: Database) -> tuple[int, int, int]:
    async with database.transaction() as repositories:
        session = repositories.singleflight._session  # noqa: SLF001 - integration SQL assertion
        jobs = await session.scalar(select(func.count(DownloadJob.id)))
        flights = await session.scalar(select(func.count(DownloadFlight.id)))
        subscribers = await session.scalar(select(func.count(JobSubscriber.id)))
        return int(jobs or 0), int(flights or 0), int(subscribers or 0)


async def test_basic_key_identity_and_request_key(database: Database) -> None:
    first_track = await _track(database)
    second_track = await _track(database, "Other")
    service = SingleFlightService(database, max_size=10)

    first = await service.submit(
        track_id=first_track,
        quality_profile=QualityProfile.MP3_320,
        request_key="delivery-1",
    )
    duplicate_request = await service.submit(
        track_id=first_track,
        quality_profile=QualityProfile.MP3_320,
        request_key="delivery-1",
    )
    another_subscriber = await service.submit(
        track_id=first_track, quality_profile=QualityProfile.MP3_320
    )
    another_quality = await service.submit(
        track_id=first_track, quality_profile=QualityProfile.MP3_128
    )
    another_track = await service.submit(
        track_id=second_track, quality_profile=QualityProfile.MP3_320
    )

    assert first.created_new_job and not first.joined_existing_flight
    assert duplicate_request.returned_existing_subscriber
    assert duplicate_request.subscriber.id == first.subscriber.id
    assert another_subscriber.download_job.id == first.download_job.id
    assert another_subscriber.subscriber.id != first.subscriber.id
    assert another_quality.download_job.id != first.download_job.id
    assert another_track.download_job.id != first.download_job.id
    assert await _counts(database) == (3, 3, 4)


async def test_request_key_validation_is_bounded(database: Database) -> None:
    track_id = await _track(database)
    service = SingleFlightService(database, max_size=10)
    with pytest.raises(InvalidRequestKeyError):
        await service.submit(
            track_id=track_id,
            quality_profile=QualityProfile.MP3_320,
            request_key="x" * 129,
        )
    assert await _counts(database) == (0, 0, 0)


async def test_concurrent_same_key_storm_creates_one_job(database: Database) -> None:
    track_id = await _track(database)
    service = SingleFlightService(database, max_size=10)
    submissions = await asyncio.gather(
        *(
            service.submit(track_id=track_id, quality_profile=QualityProfile.MP3_320)
            for _ in range(100)
        )
    )

    assert len({item.download_job.id for item in submissions}) == 1
    assert len({item.subscriber.id for item in submissions}) == 100
    assert sum(item.created_new_job for item in submissions) == 1
    assert await _counts(database) == (1, 1, 100)


async def test_concurrent_request_key_is_idempotent(database: Database) -> None:
    track_id = await _track(database)
    service = SingleFlightService(database, max_size=10)
    submissions = await asyncio.gather(
        *(
            service.submit(
                track_id=track_id,
                quality_profile=QualityProfile.LOSSLESS,
                request_key="same-request",
            )
            for _ in range(100)
        )
    )

    assert len({item.download_job.id for item in submissions}) == 1
    assert len({item.subscriber.id for item in submissions}) == 1
    assert await _counts(database) == (1, 1, 1)


async def test_queue_full_allows_join_but_rejects_new_flight(database: Database) -> None:
    first_track = await _track(database)
    second_track = await _track(database, "Other")
    service = SingleFlightService(database, max_size=1)
    first = await service.submit(track_id=first_track, quality_profile=QualityProfile.AAC_256)
    joined = await service.submit(track_id=first_track, quality_profile=QualityProfile.AAC_256)

    assert joined.download_job.id == first.download_job.id
    with pytest.raises(QueueFullError):
        await service.submit(track_id=second_track, quality_profile=QualityProfile.AAC_256)


async def test_shared_pipeline_marks_all_ready_and_closes_flight(
    database: Database, tmp_path: Path
) -> None:
    track_id = await _track(database)
    clock = ManualClock()
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    notifier = SubscriberNotifier()
    uploads = UploadQueueService(database, artifacts, clock=clock, subscriber_notifier=notifier)
    service = SingleFlightService(
        database,
        max_size=10,
        clock=clock,
        notifier=notifier,
        upload_queue=uploads,
    )
    subscribers = [
        await service.submit(track_id=track_id, quality_profile=QualityProfile.MP3_320)
        for _ in range(10)
    ]
    pipeline = RecordingPipeline(artifacts)
    downloader = DownloadWorkerBackend(
        database, pipeline, artifacts, clock=clock, subscriber_notifier=notifier
    )
    download = await downloader.claim("download-1")
    assert download is not None
    await downloader.process(download, "download-1")
    assert (await service.snapshot()).active_flights == 1
    assert (await service.subscriber_counts()).waiting == 10

    uploader = RecordingUploader()
    upload_backend = UploadWorkerBackend(
        database, uploader, uploads, clock=clock, subscriber_notifier=notifier
    )
    upload = await upload_backend.claim("upload-1")
    assert upload is not None
    await upload_backend.process(upload, "upload-1")

    assert len(pipeline.calls) == 1
    assert len(uploader.requests) == 1
    assert (await service.snapshot()).active_flights == 0
    assert (await service.subscriber_counts()).ready == 10
    statuses = [(await service.get_subscriber(item.subscriber.id)).status for item in subscribers]
    assert all(status is SubscriberStatus.READY for status in statuses)


async def test_one_cancelled_subscriber_stays_cancelled_after_success(
    database: Database, tmp_path: Path
) -> None:
    track_id = await _track(database)
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    notifier = SubscriberNotifier()
    uploads = UploadQueueService(database, artifacts, subscriber_notifier=notifier)
    service = SingleFlightService(database, max_size=10, notifier=notifier, upload_queue=uploads)
    first = await service.submit(track_id=track_id, quality_profile=QualityProfile.MP3_128)
    second = await service.submit(track_id=track_id, quality_profile=QualityProfile.MP3_128)
    assert (
        await service.cancel_subscriber(first.subscriber.id)
    ).status is SubscriberStatus.CANCELLED
    assert (await service.get_subscriber(second.subscriber.id)).status is SubscriberStatus.WAITING

    pipeline = RecordingPipeline(artifacts)
    downloader = DownloadWorkerBackend(database, pipeline, artifacts, subscriber_notifier=notifier)
    download = await downloader.claim("download-1")
    assert download is not None
    await downloader.process(download, "download-1")
    uploader = RecordingUploader()
    upload_backend = UploadWorkerBackend(database, uploader, uploads, subscriber_notifier=notifier)
    upload = await upload_backend.claim("upload-1")
    assert upload is not None
    await upload_backend.process(upload, "upload-1")

    assert (await service.get_subscriber(first.subscriber.id)).status is SubscriberStatus.CANCELLED
    assert (await service.get_subscriber(second.subscriber.id)).status is SubscriberStatus.READY


async def test_download_terminal_failure_fails_waiters_and_closes_flight(
    database: Database, tmp_path: Path
) -> None:
    track_id = await _track(database)
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    notifier = SubscriberNotifier()
    service = SingleFlightService(database, max_size=10, notifier=notifier)
    subscribers = [
        await service.submit(track_id=track_id, quality_profile=QualityProfile.MP3_128)
        for _ in range(3)
    ]
    pipeline = FailingPipeline()
    backend = DownloadWorkerBackend(database, pipeline, artifacts, subscriber_notifier=notifier)
    download = await backend.claim("download-1")
    assert download is not None
    await backend.process(download, "download-1")

    statuses = [(await service.get_subscriber(item.subscriber.id)).status for item in subscribers]
    assert statuses == [SubscriberStatus.FAILED] * 3
    assert pipeline.calls == 1
    assert (await service.snapshot()).active_flights == 0


async def test_upload_retry_keeps_waiters_then_success_marks_ready(
    database: Database, tmp_path: Path
) -> None:
    track_id = await _track(database)
    clock = ManualClock()
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    notifier = SubscriberNotifier()
    uploads = UploadQueueService(database, artifacts, clock=clock, subscriber_notifier=notifier)
    service = SingleFlightService(
        database, max_size=10, clock=clock, notifier=notifier, upload_queue=uploads
    )
    submitted = await service.submit(track_id=track_id, quality_profile=QualityProfile.AAC_256)
    pipeline = RecordingPipeline(artifacts)
    download_backend = DownloadWorkerBackend(
        database, pipeline, artifacts, clock=clock, subscriber_notifier=notifier
    )
    download = await download_backend.claim("download-1")
    assert download is not None
    await download_backend.process(download, "download-1")
    uploader = ScriptedUploader([UploadRetryableError(), None])
    upload_backend = UploadWorkerBackend(
        database, uploader, uploads, clock=clock, subscriber_notifier=notifier
    )
    first = await upload_backend.claim("upload-1")
    assert first is not None
    await upload_backend.process(first, "upload-1")
    assert (
        await service.get_subscriber(submitted.subscriber.id)
    ).status is SubscriberStatus.WAITING
    assert (await service.snapshot()).active_flights == 1

    clock.advance(2)
    second = await upload_backend.claim("upload-1")
    assert second is not None
    await upload_backend.process(second, "upload-1")
    assert (await service.get_subscriber(submitted.subscriber.id)).status is SubscriberStatus.READY
    assert len(uploader.requests) == 2
    assert (await service.snapshot()).active_flights == 0


async def test_shared_download_retry_rechecks_runtime_once_per_attempt(
    database: Database, tmp_path: Path
) -> None:
    track_id = await _track(database)
    clock = ManualClock()
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    notifier = SubscriberNotifier()
    uploads = UploadQueueService(database, artifacts, clock=clock, subscriber_notifier=notifier)
    service = SingleFlightService(
        database, max_size=10, clock=clock, notifier=notifier, upload_queue=uploads
    )
    subscribers = [
        await service.submit(track_id=track_id, quality_profile=QualityProfile.LOSSLESS)
        for _ in range(10)
    ]
    pipeline = RetryingPipeline(artifacts)
    backend = DownloadWorkerBackend(
        database, pipeline, artifacts, clock=clock, subscriber_notifier=notifier
    )
    first = await backend.claim("download-1")
    assert first is not None
    await backend.process(first, "download-1")
    assert (await service.subscriber_counts()).waiting == 10
    assert (await service.snapshot()).active_flights == 1

    clock.advance(2)
    second = await backend.claim("download-1")
    assert second is not None
    await backend.process(second, "download-1")
    assert len(pipeline.calls) == 2
    assert len(await uploads.list_upload_jobs()) == 1

    uploader = RecordingUploader()
    upload_backend = UploadWorkerBackend(
        database, uploader, uploads, clock=clock, subscriber_notifier=notifier
    )
    upload = await upload_backend.claim("upload-1")
    assert upload is not None
    await upload_backend.process(upload, "upload-1")
    statuses = [(await service.get_subscriber(item.subscriber.id)).status for item in subscribers]
    assert statuses == [SubscriberStatus.READY] * 10


async def test_last_subscriber_cancels_queued_upload_and_releases_artifact(
    database: Database, tmp_path: Path
) -> None:
    track_id = await _track(database)
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    notifier = SubscriberNotifier()
    uploads = UploadQueueService(database, artifacts, subscriber_notifier=notifier)
    service = SingleFlightService(database, max_size=10, notifier=notifier, upload_queue=uploads)
    submitted = await service.submit(track_id=track_id, quality_profile=QualityProfile.MP3_320)
    pipeline = RecordingPipeline(artifacts)
    backend = DownloadWorkerBackend(database, pipeline, artifacts, subscriber_notifier=notifier)
    download = await backend.claim("download-1")
    assert download is not None
    await backend.process(download, "download-1")
    upload = (await uploads.list_upload_jobs())[0]
    async with database.transaction() as repositories:
        persisted = await repositories.upload_jobs.get(upload.id)
        assert persisted is not None
        artifact_root = artifacts.root / persisted.artifact_job_id
    assert artifact_root.exists()

    await service.cancel_subscriber(submitted.subscriber.id)
    assert (await uploads.get_upload_job(upload.id)).status is QueueJobStatus.CANCELLED
    assert not artifact_root.exists()
    assert (await service.snapshot()).active_flights == 0


async def test_last_subscriber_cancels_queued_and_running_work(database: Database) -> None:
    first_track = await _track(database)
    second_track = await _track(database, "Other")
    service = SingleFlightService(database, max_size=10)
    queued = await service.submit(track_id=first_track, quality_profile=QualityProfile.MP3_128)
    await service.cancel_subscriber(queued.subscriber.id)
    assert (
        await DownloadQueueService(database, max_size=10).get_download_job(queued.download_job.id)
    ).status is QueueJobStatus.CANCELLED

    running = await service.submit(track_id=second_track, quality_profile=QualityProfile.MP3_128)
    now = datetime.now(UTC)
    async with database.transaction() as repositories:
        claimed = await repositories.download_jobs.claim(
            worker_id="download-1", now=now, lease_expires_at=now + timedelta(minutes=1)
        )
    assert claimed is not None
    canceller = RecordingCanceller()
    service.attach_operation_canceller(canceller)
    await service.cancel_subscriber(running.subscriber.id)
    job = await DownloadQueueService(database, max_size=10).get_download_job(
        running.download_job.id
    )
    assert job.status is QueueJobStatus.RUNNING and job.cancel_requested
    assert canceller.downloads == [running.download_job.id]
    assert (await service.snapshot()).active_flights == 0


async def test_admin_cancellation_propagates_to_all_waiters(database: Database) -> None:
    track_id = await _track(database)
    notifier = SubscriberNotifier()
    service = SingleFlightService(database, max_size=10, notifier=notifier)
    subscribers = [
        await service.submit(track_id=track_id, quality_profile=QualityProfile.MP3_320)
        for _ in range(3)
    ]
    queue = DownloadQueueService(database, max_size=10, subscriber_notifier=notifier)
    await queue.cancel(subscribers[0].download_job.id)

    statuses = [(await service.get_subscriber(item.subscriber.id)).status for item in subscribers]
    assert all(status is SubscriberStatus.CANCELLED for status in statuses)
    assert (await service.snapshot()).active_flights == 0


@pytest.mark.parametrize(
    ("upload_status", "expected"),
    [
        (QueueJobStatus.SUCCEEDED, SubscriberStatus.READY),
        (QueueJobStatus.FAILED, SubscriberStatus.FAILED),
        (QueueJobStatus.CANCELLED, SubscriberStatus.CANCELLED),
    ],
)
async def test_reconcile_stale_upload_terminal_state(
    database: Database, upload_status: QueueJobStatus, expected: SubscriberStatus
) -> None:
    track_id = await _track(database)
    service = SingleFlightService(database, max_size=10)
    submitted = await service.submit(track_id=track_id, quality_profile=QualityProfile.LOSSLESS)
    now = datetime.now(UTC)
    async with database.transaction() as repositories:
        job = await repositories.download_jobs.get(submitted.download_job.id)
        assert job is not None
        job.status = QueueJobStatus.SUCCEEDED
        job.finished_at = now
        repositories.upload_jobs._session.add(  # noqa: SLF001 - crash-window fixture
            UploadJob(
                download_job_id=job.id,
                track_id=job.track_id,
                quality_profile=job.quality_profile,
                status=upload_status,
                artifact_job_id="0" * 32,
                artifact_path="0/output/final.mp3",
                queued_at=now,
                available_at=now,
                finished_at=now,
                last_error_code=(
                    QueueErrorCode.UPLOAD_TERMINAL.value
                    if upload_status is QueueJobStatus.FAILED
                    else None
                ),
            )
        )
    recreated = SingleFlightService(database, max_size=10)
    assert await recreated.reconcile() == 1
    subscriber = await recreated.get_subscriber(submitted.subscriber.id)
    assert subscriber.status is expected
    assert (await recreated.snapshot()).active_flights == 0
    assert await recreated.reconcile() == 0


async def test_reconcile_stale_download_failure_allows_fresh_submission(
    database: Database,
) -> None:
    track_id = await _track(database)
    service = SingleFlightService(database, max_size=10)
    submitted = await service.submit(track_id=track_id, quality_profile=QualityProfile.MP3_320)
    async with database.engine.begin() as connection:
        await connection.execute(
            update(DownloadJob)
            .where(DownloadJob.id == submitted.download_job.id)
            .values(
                status=QueueJobStatus.FAILED,
                finished_at=datetime.now(UTC),
                last_error_code="DOWNLOAD_WORKER_ERROR",
            )
        )

    replacement = await service.submit(track_id=track_id, quality_profile=QualityProfile.MP3_320)
    assert replacement.download_job.id != submitted.download_job.id
    failed = await service.get_subscriber(submitted.subscriber.id)
    assert failed.status is SubscriberStatus.FAILED
    assert failed.last_error_code == "DOWNLOAD_WORKER_ERROR"


async def test_wait_notification_timeout_and_task_cancellation(database: Database) -> None:
    track_id = await _track(database)
    notifier = SubscriberNotifier()
    service = SingleFlightService(database, max_size=10, notifier=notifier, wait_poll_seconds=10)
    first = await service.submit(track_id=track_id, quality_profile=QualityProfile.MP3_128)
    second = await service.submit(track_id=track_id, quality_profile=QualityProfile.MP3_128)
    waiter = asyncio.create_task(service.wait(first.subscriber.id, timeout=1))
    await asyncio.sleep(0)
    await service.cancel_subscriber(first.subscriber.id)
    assert (await waiter).status is SubscriberStatus.CANCELLED

    with pytest.raises(TimeoutError):
        await service.wait(second.subscriber.id, timeout=0.01)
    assert (await service.get_subscriber(second.subscriber.id)).status is SubscriberStatus.WAITING

    cancelled_waiter = asyncio.create_task(service.wait(second.subscriber.id))
    await asyncio.sleep(0)
    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter
    assert (await service.get_subscriber(second.subscriber.id)).status is SubscriberStatus.WAITING


async def test_cancellation_submission_race_never_joins_cancel_requested_job(
    database: Database,
) -> None:
    track_id = await _track(database)
    service = SingleFlightService(database, max_size=10)
    first = await service.submit(track_id=track_id, quality_profile=QualityProfile.AAC_256)
    cancelled, replacement = await asyncio.gather(
        service.cancel_subscriber(first.subscriber.id),
        service.submit(track_id=track_id, quality_profile=QualityProfile.AAC_256),
    )
    assert cancelled.status is SubscriberStatus.CANCELLED
    replacement_job = await DownloadQueueService(database, max_size=10).get_download_job(
        replacement.download_job.id
    )
    assert not replacement_job.cancel_requested
    assert replacement.subscriber.status is SubscriberStatus.WAITING
    assert (await service.snapshot()).active_flights == 1
