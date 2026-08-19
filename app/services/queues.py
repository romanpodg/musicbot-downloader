"""Typed Stage 7 queue, upload, and runtime-setting service boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from app.config import Settings
from app.core.enums import QualityProfile, QueueJobStatus
from app.core.exceptions import QueueJobNotFoundError, WorkerLimitError
from app.core.models import (
    DownloadJobView,
    QueueStatusCounts,
    UploadJobView,
    UploadRequest,
    UploadResult,
    WorkerSettingsSnapshot,
    WorkerSettingValues,
)
from app.services.artifacts import ArtifactPathError, DownloadArtifactManager
from app.storage import Database
from app.storage.models import DownloadJob, UploadJob
from app.storage.models.base import utc_now

MAX_PAGE_SIZE = 100
MIN_WORKERS = 1


@dataclass(frozen=True, slots=True)
class WorkerSettingMutation:
    previous: int
    current: int
    snapshot: WorkerSettingsSnapshot


class UploadExecutor(Protocol):
    async def upload(self, request: UploadRequest) -> UploadResult: ...


class WorkerPoolResizer(Protocol):
    async def resize_download(self, workers: int) -> None: ...

    async def resize_upload(self, workers: int) -> None: ...


class SubscriberLifecycleNotifier(Protocol):
    async def notify_all(self) -> None: ...


class DownloadQueueService:
    def __init__(
        self,
        database: Database,
        *,
        max_size: int,
        clock: Callable[[], datetime] = utc_now,
        wake_event: asyncio.Event | None = None,
        subscriber_notifier: SubscriberLifecycleNotifier | None = None,
    ) -> None:
        self._database = database
        self._max_size = max_size
        self._clock = clock
        self._wake_event = wake_event
        self._subscriber_notifier = subscriber_notifier

    async def submit(self, *, track_id: int, quality_profile: QualityProfile) -> DownloadJobView:
        if not isinstance(quality_profile, QualityProfile):
            raise ValueError("invalid quality profile")
        async with self._database.transaction() as repositories:
            job = await repositories.download_jobs.submit(
                track_id=track_id,
                quality_profile=quality_profile,
                max_active=self._max_size,
                now=self._clock(),
            )
            view = download_job_view(job)
        self._wake()
        return view

    async def get_download_job(self, job_id: int) -> DownloadJobView:
        async with self._database.transaction() as repositories:
            job = await repositories.download_jobs.get(job_id)
            if job is None:
                raise QueueJobNotFoundError()
            return download_job_view(job)

    async def list_download_jobs(
        self, *, offset: int = 0, limit: int = 50
    ) -> tuple[DownloadJobView, ...]:
        _validate_page(offset, limit)
        async with self._database.transaction() as repositories:
            jobs = await repositories.download_jobs.list(offset=offset, limit=limit)
            return tuple(download_job_view(job) for job in jobs)

    async def counts(self) -> QueueStatusCounts:
        async with self._database.transaction() as repositories:
            return _counts(await repositories.download_jobs.counts())

    async def cancel(self, job_id: int) -> QueueJobStatus:
        reconciled = False
        async with self._database.transaction() as repositories:
            status = await repositories.download_jobs.cancel(job_id, self._clock())
            if status is None:
                raise QueueJobNotFoundError()
            reconciled = await repositories.singleflight.reconcile_download_job(
                job_id, self._clock()
            )
        self._wake()
        if reconciled and self._subscriber_notifier is not None:
            await self._subscriber_notifier.notify_all()
        return status

    def _wake(self) -> None:
        if self._wake_event is not None:
            self._wake_event.set()


class UploadQueueService:
    def __init__(
        self,
        database: Database,
        artifacts: DownloadArtifactManager,
        *,
        clock: Callable[[], datetime] = utc_now,
        wake_event: asyncio.Event | None = None,
        subscriber_notifier: SubscriberLifecycleNotifier | None = None,
    ) -> None:
        self._database = database
        self._artifacts = artifacts
        self._clock = clock
        self._wake_event = wake_event
        self._subscriber_notifier = subscriber_notifier

    async def get_upload_job(self, job_id: int) -> UploadJobView:
        async with self._database.transaction() as repositories:
            job = await repositories.upload_jobs.get(job_id)
            if job is None:
                raise QueueJobNotFoundError()
            return _upload_view(job)

    async def list_upload_jobs(
        self, *, offset: int = 0, limit: int = 50
    ) -> tuple[UploadJobView, ...]:
        _validate_page(offset, limit)
        async with self._database.transaction() as repositories:
            jobs = await repositories.upload_jobs.list(offset=offset, limit=limit)
            return tuple(_upload_view(job) for job in jobs)

    async def counts(self) -> QueueStatusCounts:
        async with self._database.transaction() as repositories:
            return _counts(await repositories.upload_jobs.counts())

    async def cancel(self, job_id: int) -> QueueJobStatus:
        artifact: tuple[str, str] | None = None
        reconciled = False
        async with self._database.transaction() as repositories:
            job = await repositories.upload_jobs.get(job_id)
            if job is None:
                raise QueueJobNotFoundError()
            previous = job.status
            artifact = (job.artifact_job_id, job.artifact_path)
            status = await repositories.upload_jobs.cancel(job_id, self._clock())
            if status is None:
                raise QueueJobNotFoundError()
            reconciled = await repositories.singleflight.reconcile_download_job(
                job.download_job_id, self._clock()
            )
        if previous is QueueJobStatus.QUEUED and status is QueueJobStatus.CANCELLED:
            self.release_owned(*artifact)
        self._wake()
        if reconciled and self._subscriber_notifier is not None:
            await self._subscriber_notifier.notify_all()
        return status

    def release_owned(self, artifact_job_id: str, stored_path: str) -> bool:
        try:
            path = self._resolve_stored_path(artifact_job_id, stored_path)
        except ArtifactPathError:
            return False
        if path.exists():
            self._artifacts.release(artifact_job_id)
        return True

    def validate_artifact(
        self,
        artifact_job_id: str,
        stored_path: str,
        *,
        expected_size: int | None = None,
    ) -> Path:
        path = self.validate_artifact_reference(artifact_job_id, stored_path)
        if not path.is_file() or path.stat().st_size <= 0:
            raise FileNotFoundError()
        if expected_size is not None and path.stat().st_size != expected_size:
            raise ArtifactPathError()
        return path

    def validate_artifact_reference(self, artifact_job_id: str, stored_path: str) -> Path:
        return self._resolve_stored_path(artifact_job_id, stored_path)

    def _resolve_stored_path(self, artifact_job_id: str, stored_path: str) -> Path:
        relative = Path(stored_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ArtifactPathError()
        resolved = self._artifacts.ensure_owned(self._artifacts.root / relative, artifact_job_id)
        expected_root = (self._artifacts.root / artifact_job_id / "output").resolve()
        if resolved.parent != expected_root or not resolved.name.startswith("final."):
            raise ArtifactPathError()
        return resolved

    def _wake(self) -> None:
        if self._wake_event is not None:
            self._wake_event.set()


class WorkerSettingsService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self._database = database
        self._settings = settings
        self._resizer: WorkerPoolResizer | None = None
        self._lock = asyncio.Lock()

    def attach_resizer(self, resizer: WorkerPoolResizer) -> None:
        self._resizer = resizer

    async def initialize(self) -> WorkerSettingsSnapshot:
        async with self._lock:
            async with self._database.transaction() as repositories:
                values = await repositories.runtime_settings.initialize(
                    download_default=self._settings.download_workers_default,
                    upload_default=self._settings.upload_workers_default,
                    download_max=self._settings.download_workers_max,
                    upload_max=self._settings.upload_workers_max,
                )
                return self._snapshot(values.download_workers, values.upload_workers)

    async def get_values(self) -> WorkerSettingsSnapshot:
        async with self._database.transaction() as repositories:
            values = await repositories.runtime_settings.get()
        if values is None:
            return await self.initialize()
        return self._snapshot(values.download_workers, values.upload_workers)

    async def set_download_workers(self, workers: int) -> WorkerSettingsSnapshot:
        return (await self.update_download_workers(workers)).snapshot

    async def update_download_workers(self, workers: int) -> WorkerSettingMutation:
        self._validate(workers, self._settings.download_workers_max)
        async with self._lock:
            await self.initialize_if_missing()
            async with self._database.transaction() as repositories:
                values = await repositories.runtime_settings.get()
                if values is None:
                    raise RuntimeError("runtime settings are not initialized")
                previous = values.download_workers
                await repositories.runtime_settings.set_download_workers(workers)
            if self._resizer is not None:
                await self._resizer.resize_download(workers)
            snapshot = await self.get_values()
        return WorkerSettingMutation(previous, workers, snapshot)

    async def set_upload_workers(self, workers: int) -> WorkerSettingsSnapshot:
        return (await self.update_upload_workers(workers)).snapshot

    async def update_upload_workers(self, workers: int) -> WorkerSettingMutation:
        self._validate(workers, self._settings.upload_workers_max)
        async with self._lock:
            await self.initialize_if_missing()
            async with self._database.transaction() as repositories:
                values = await repositories.runtime_settings.get()
                if values is None:
                    raise RuntimeError("runtime settings are not initialized")
                previous = values.upload_workers
                await repositories.runtime_settings.set_upload_workers(workers)
            if self._resizer is not None:
                await self._resizer.resize_upload(workers)
            snapshot = await self.get_values()
        return WorkerSettingMutation(previous, workers, snapshot)

    async def adjust_download_workers(self, delta: int) -> WorkerSettingMutation:
        self._validate_delta(delta)
        async with self._lock:
            await self.initialize_if_missing()
            async with self._database.transaction() as repositories:
                previous, current = await repositories.runtime_settings.adjust_download_workers(
                    delta,
                    minimum=MIN_WORKERS,
                    maximum=self._settings.download_workers_max,
                )
            if current != previous and self._resizer is not None:
                await self._resizer.resize_download(current)
            snapshot = await self.get_values()
        return WorkerSettingMutation(previous, current, snapshot)

    async def adjust_upload_workers(self, delta: int) -> WorkerSettingMutation:
        self._validate_delta(delta)
        async with self._lock:
            await self.initialize_if_missing()
            async with self._database.transaction() as repositories:
                previous, current = await repositories.runtime_settings.adjust_upload_workers(
                    delta,
                    minimum=MIN_WORKERS,
                    maximum=self._settings.upload_workers_max,
                )
            if current != previous and self._resizer is not None:
                await self._resizer.resize_upload(current)
            snapshot = await self.get_values()
        return WorkerSettingMutation(previous, current, snapshot)

    async def initialize_if_missing(self) -> None:
        async with self._database.transaction() as repositories:
            await repositories.runtime_settings.initialize(
                download_default=self._settings.download_workers_default,
                upload_default=self._settings.upload_workers_default,
                download_max=self._settings.download_workers_max,
                upload_max=self._settings.upload_workers_max,
            )

    @staticmethod
    def _validate(workers: int, maximum: int) -> None:
        if workers < MIN_WORKERS or workers > maximum:
            raise WorkerLimitError()

    @staticmethod
    def _validate_delta(delta: int) -> None:
        if delta not in {-1, 1}:
            raise ValueError("worker adjustment must be -1 or +1")

    def _snapshot(self, download: int, upload: int) -> WorkerSettingsSnapshot:
        return WorkerSettingsSnapshot(
            download=WorkerSettingValues(
                download,
                self._settings.download_workers_default,
                self._settings.download_workers_max,
            ),
            upload=WorkerSettingValues(
                upload,
                self._settings.upload_workers_default,
                self._settings.upload_workers_max,
            ),
        )


def download_job_view(job: DownloadJob) -> DownloadJobView:
    return DownloadJobView(
        job.id,
        job.track_id,
        job.quality_profile,
        job.status,
        job.attempt_count,
        job.queued_at,
        job.available_at,
        job.started_at,
        job.finished_at,
        job.last_error_code,
        job.cancel_requested,
    )


def _upload_view(job: UploadJob) -> UploadJobView:
    return UploadJobView(
        job.id,
        job.download_job_id,
        job.track_id,
        job.quality_profile,
        job.status,
        job.attempt_count,
        job.queued_at,
        job.available_at,
        job.started_at,
        job.finished_at,
        job.last_error_code,
        job.cancel_requested,
    )


def _counts(values: dict[QueueJobStatus, int]) -> QueueStatusCounts:
    return QueueStatusCounts(
        queued=values.get(QueueJobStatus.QUEUED, 0),
        running=values.get(QueueJobStatus.RUNNING, 0),
        succeeded=values.get(QueueJobStatus.SUCCEEDED, 0),
        failed=values.get(QueueJobStatus.FAILED, 0),
        cancelled=values.get(QueueJobStatus.CANCELLED, 0),
    )


def _validate_page(offset: int, limit: int) -> None:
    if offset < 0 or limit < 1 or limit > MAX_PAGE_SIZE:
        raise ValueError("invalid pagination")
