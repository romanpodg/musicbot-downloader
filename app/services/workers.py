"""Persistent queue processors and dynamically resizable async worker pools."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from app.config import Settings
from app.core.download_preferences import EffectiveDownloadProfile
from app.core.enums import DownloadFailureCode, QualityProfile, QueueErrorCode, QueueJobStatus
from app.core.exceptions import (
    DownloadPipelineError,
    TrackNotFound,
    UploadRetryableError,
    UploadTerminalError,
)
from app.core.models import (
    DownloadArtifactMetadata,
    DownloadResult,
    QueueRuntimeSnapshot,
    UploadRequest,
    WorkerPoolSnapshot,
)
from app.core.provider_resolution import CanonicalMediaIdentity, ProviderCandidateRanker
from app.services.artifact_cleanup import StaleArtifactCleanupService
from app.services.artifacts import ArtifactPathError, DownloadArtifactManager
from app.services.download_lifecycle import DownloadLifecycleService
from app.services.provider_candidates import ProviderCandidateResolver
from app.services.queues import (
    DownloadQueueService,
    SubscriberLifecycleNotifier,
    UploadExecutor,
    UploadQueueService,
    WorkerSettingsService,
)
from app.services.runtime_prerequisites import TemporaryDiskGuard
from app.storage import Database
from app.storage.models import DownloadJob, UploadJob
from app.storage.models.base import utc_now

if TYPE_CHECKING:
    from app.services.singleflight import SingleFlightService

logger = logging.getLogger(__name__)

DOWNLOAD_JOB_MAX_ATTEMPTS = 3
UPLOAD_JOB_MAX_ATTEMPTS = 3
DEFAULT_LEASE_SECONDS = 900.0
DEFAULT_HEARTBEAT_SECONDS = 60.0
DEFAULT_POLL_SECONDS = 0.5
DEFAULT_SHUTDOWN_SECONDS = 30.0

RETRYABLE_DOWNLOAD_CODES = frozenset(
    {
        DownloadFailureCode.NO_AVAILABLE_PROVIDER,
        DownloadFailureCode.AUTH_REQUIRED,
        DownloadFailureCode.PROVIDER_UNAVAILABLE,
        DownloadFailureCode.SOURCE_UNAVAILABLE,
        DownloadFailureCode.PROVIDER_ERROR,
        DownloadFailureCode.PROVIDER_TEMPORARY,
        DownloadFailureCode.PROVIDER_RATE_LIMITED,
        DownloadFailureCode.NETWORK,
        DownloadFailureCode.DOWNLOAD_TIMEOUT,
        DownloadFailureCode.TEMP_STORAGE_UNAVAILABLE,
    }
)


class DownloadPipelineBoundary(Protocol):
    async def download(
        self,
        track_id: int,
        quality_profile: QualityProfile,
        profile: EffectiveDownloadProfile | None = None,
    ) -> DownloadResult: ...


class Stage25ExecutionBoundary(Protocol):
    """Authoritative source execution seam for Stage 25-enabled workers."""

    async def download(self, job: DownloadJob) -> DownloadResult: ...


class WorkerBackend(Protocol):
    wake_event: asyncio.Event

    async def claim(self, worker_id: str) -> DownloadJob | UploadJob | None: ...

    async def process(self, job: DownloadJob | UploadJob, worker_id: str) -> None: ...

    async def heartbeat(self, job_id: int, worker_id: str) -> bool: ...


class DownloadWorkerBackend:
    def __init__(
        self,
        database: Database,
        pipeline: DownloadPipelineBoundary,
        artifacts: DownloadArtifactManager,
        *,
        clock: Callable[[], datetime] = utc_now,
        max_attempts: int = DOWNLOAD_JOB_MAX_ATTEMPTS,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        wake_event: asyncio.Event | None = None,
        subscriber_notifier: SubscriberLifecycleNotifier | None = None,
        candidate_resolver: ProviderCandidateResolver | None = None,
        candidate_ranker: ProviderCandidateRanker | None = None,
        stage25_executor: Stage25ExecutionBoundary | None = None,
        per_user_active_limit: int | None = None,
        disk_guard: TemporaryDiskGuard | None = None,
        artifact_cleanup: StaleArtifactCleanupService | None = None,
    ) -> None:
        self._database = database
        self._pipeline = pipeline
        self._artifacts = artifacts
        self._clock = clock
        self._max_attempts = max_attempts
        self._lease = timedelta(seconds=lease_seconds)
        self.wake_event = wake_event or asyncio.Event()
        self._subscriber_notifier = subscriber_notifier
        self._candidate_resolver = candidate_resolver
        self._candidate_ranker = candidate_ranker or ProviderCandidateRanker()
        self._stage25_executor = stage25_executor
        self._per_user_active_limit = per_user_active_limit
        self._disk_guard = disk_guard
        self._artifact_cleanup = artifact_cleanup

    async def claim(self, worker_id: str) -> DownloadJob | None:
        now = self._clock()
        async with self._database.transaction() as repositories:
            # Serialize eligibility + claim in SQLite's single-runtime model.
            await repositories.download_jobs.begin_immediate()
            await repositories.download_jobs.recover_expired(now, self._max_attempts)
            reconciled = await repositories.singleflight.reconcile_all(now)
            job = await repositories.download_jobs.claim(
                worker_id=worker_id,
                now=now,
                lease_expires_at=now + self._lease,
                per_user_limit=self._per_user_active_limit,
            )
        await self._notify_if(reconciled > 0)
        return job

    async def heartbeat(self, job_id: int, worker_id: str) -> bool:
        async with self._database.transaction() as repositories:
            return await repositories.download_jobs.heartbeat(
                job_id, worker_id, self._clock() + self._lease
            )

    async def process(self, job: DownloadJob | UploadJob, worker_id: str) -> None:
        if not isinstance(job, DownloadJob):
            raise TypeError("download worker received wrong job type")
        result: DownloadResult | None = None
        stage25_request_id: int | None = None
        try:
            await self._ensure_storage_available()
            stage25_request_id = await self._prepare_stage25(job)
            # A retry always asks Stage 6 to resolve current runtime/auth state afresh.
            if self._stage25_executor is not None:
                result = await self._stage25_executor.download(job)
            else:
                async with self._database.transaction() as repositories:
                    request = await repositories.download_lifecycle.latest_request_for_track(
                        job.track_id
                    )
                try:
                    result = await self._pipeline.download(
                        job.track_id,
                        job.quality_profile,
                        request.effective_profile if request is not None else None,
                    )
                except TypeError as exc:
                    if "positional" not in str(exc) and "argument" not in str(exc):
                        raise
                    result = await self._pipeline.download(job.track_id, job.quality_profile)
            if self._stage25_executor is None:
                await self._audit_stage25_attempts(job, stage25_request_id, result.attempts)
            stored_path = self._validate_result(result, job)
            try:
                async with self._database.transaction() as repositories:
                    upload = await repositories.download_jobs.handoff(
                        job_id=job.id,
                        worker_id=worker_id,
                        artifact_job_id=result.job_id,
                        artifact_path=stored_path,
                        now=self._clock(),
                        artifact=_artifact_metadata(result),
                    )
                if upload is None:
                    self._artifacts.release(result.job_id)
                    await self._retry(
                        job,
                        worker_id,
                        QueueErrorCode.DOWNLOAD_WORKER_ERROR.value,
                        retryable=True,
                    )
                    return
            except Exception:
                self._artifacts.release(result.job_id)
                await self._retry(
                    job,
                    worker_id,
                    QueueErrorCode.DOWNLOAD_PERSISTENCE_ERROR.value,
                    retryable=True,
                )
                return
            self.wake_event.set()
            logger.info(
                "Download artifact handed to upload queue",
                extra={
                    "job_id": job.id,
                    "job_type": "download",
                    "track_id": job.track_id,
                    "quality_profile": job.quality_profile.value,
                    "worker_id": worker_id,
                    "attempt": job.attempt_count,
                    "status": QueueJobStatus.SUCCEEDED.value,
                },
            )
        except asyncio.CancelledError:
            if result is not None:
                self._artifacts.release(result.job_id)
            await self._retry(
                job,
                worker_id,
                QueueErrorCode.DOWNLOAD_WORKER_ERROR.value,
                retryable=True,
            )
        except DownloadPipelineError as exc:
            if self._stage25_executor is None:
                await self._audit_stage25_attempts(job, stage25_request_id, exc.attempts)
            await self._retry(
                job,
                worker_id,
                exc.code.value,
                retryable=exc.code in RETRYABLE_DOWNLOAD_CODES,
            )
        except TrackNotFound:
            await self._fail(job, worker_id, QueueErrorCode.DOWNLOAD_PIPELINE_ERROR.value)
        except (ArtifactPathError, OSError, ValueError):
            if result is not None:
                self._artifacts.release(result.job_id)
            await self._fail(job, worker_id, QueueErrorCode.DOWNLOAD_PIPELINE_ERROR.value)
        except Exception:
            if result is not None:
                self._artifacts.release(result.job_id)
            logger.error(
                "Unexpected download worker failure",
                extra={"job_id": job.id, "job_type": "download", "worker_id": worker_id},
            )
            await self._fail(job, worker_id, QueueErrorCode.DOWNLOAD_WORKER_ERROR.value)

    async def _ensure_storage_available(self) -> None:
        """Sweep only safely stale artifacts once before deferring new media work."""

        if self._disk_guard is None:
            return
        try:
            self._disk_guard.ensure_available()
            return
        except OSError:
            if self._artifact_cleanup is not None:
                try:
                    await self._artifact_cleanup.sweep()
                except Exception:
                    logger.exception("stale artifact cleanup failed under storage pressure")
            try:
                self._disk_guard.ensure_available()
            except OSError as second_error:
                raise DownloadPipelineError(
                    DownloadFailureCode.TEMP_STORAGE_UNAVAILABLE
                ) from second_error
            logger.info("storage pressure cleared by stale artifact cleanup")

    async def _prepare_stage25(self, job: DownloadJob) -> int | None:
        """Resolve/rank Stage 25 candidates after cache miss and before source execution."""
        if self._candidate_resolver is None:
            return None
        async with self._database.transaction() as repositories:
            track = await repositories.tracks.get_track_by_id(job.track_id)
            request = await repositories.download_lifecycle.latest_request_for_track(job.track_id)
        if track is None:
            return request.id if request is not None else None
        identity = CanonicalMediaIdentity.from_values(
            title=track.title or "Unknown",
            artist=track.artist or "Unknown",
            album=track.album,
            isrc=track.isrc,
            duration_ms=track.duration_ms,
        )
        try:
            candidates = await self._candidate_resolver.resolve(
                identity,
                source_provider=request.provider if request is not None else None,
                source_media_id=request.provider_media_id if request is not None else None,
                request_id=request.id if request is not None else None,
            )
            self._candidate_ranker.rank(
                candidates,
                source_provider=request.provider if request is not None else None,
                profile=request.effective_profile if request is not None else None,
                exact_replay=bool(request and request.replay_of_request_id),
            )
        except Exception:
            # Discovery is advisory; the established source pipeline remains viable.
            logger.info(
                "Stage 25 candidate preparation unavailable", extra={"track_id": job.track_id}
            )
        return request.id if request is not None else None

    async def _audit_stage25_attempts(
        self, job: DownloadJob, request_id: int | None, attempts: object
    ) -> None:
        if request_id is None or not isinstance(attempts, tuple):
            return
        async with self._database.transaction() as repositories:
            lifecycle = await repositories.download_lifecycle.get_job_for_request(request_id)
            candidates = await repositories.provider_resolution.list_candidates(request_id)
            if lifecycle is None:
                return
            by_key = {(row.provider, row.provider_media_id): row for row in candidates}
            for item in attempts:
                key = (item.provider.value, item.provider_track_id)
                candidate = by_key.get(key)
                if candidate is None:
                    continue
                number = await repositories.provider_resolution.next_attempt_number(lifecycle.id)
                row = await repositories.provider_resolution.start_attempt(
                    job_id=lifecycle.id,
                    candidate_id=candidate.id,
                    attempt_number=number,
                    provider_account_id=None,
                    now=self._clock(),
                )
                await repositories.provider_resolution.finish_attempt(
                    row.id,
                    status=item.status.value,
                    now=self._clock(),
                    failure_code=item.failure_code.value if item.failure_code else None,
                )

    def _validate_result(self, result: DownloadResult, job: DownloadJob) -> str:
        if result.track_id != job.track_id or result.requested_profile is not job.quality_profile:
            raise ValueError("download result identity mismatch")
        resolved = self._artifacts.ensure_owned(result.file_path, result.job_id)
        expected_parent = (self._artifacts.root / result.job_id / "output").resolve()
        if resolved.parent != expected_parent or not resolved.name.startswith("final."):
            raise ArtifactPathError()
        if not resolved.is_file() or resolved.stat().st_size <= 0:
            raise OSError("invalid artifact")
        return resolved.relative_to(self._artifacts.root).as_posix()

    async def _retry(
        self,
        job: DownloadJob,
        worker_id: str,
        code: str,
        *,
        retryable: bool,
    ) -> None:
        now = self._clock()
        if retryable:
            available_at = now + _retry_delay(job.attempt_count)
            async with self._database.transaction() as repositories:
                await repositories.download_jobs.retry_or_fail(
                    job_id=job.id,
                    worker_id=worker_id,
                    now=now,
                    available_at=available_at,
                    error_code=code,
                    max_attempts=self._max_attempts,
                )
                reconciled = await repositories.singleflight.reconcile_download_job(job.id, now)
            await self._notify_if(reconciled)
        else:
            await self._fail(job, worker_id, code)
        self.wake_event.set()

    async def _fail(self, job: DownloadJob, worker_id: str, code: str) -> None:
        async with self._database.transaction() as repositories:
            await repositories.download_jobs.fail(
                job_id=job.id, worker_id=worker_id, now=self._clock(), error_code=code
            )
            reconciled = await repositories.singleflight.reconcile_download_job(
                job.id, self._clock()
            )
        await self._notify_if(reconciled)

    async def _notify_if(self, changed: bool) -> None:
        if changed and self._subscriber_notifier is not None:
            await self._subscriber_notifier.notify_all()


class UploadWorkerBackend:
    def __init__(
        self,
        database: Database,
        executor: UploadExecutor,
        queue: UploadQueueService,
        *,
        clock: Callable[[], datetime] = utc_now,
        max_attempts: int = UPLOAD_JOB_MAX_ATTEMPTS,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        wake_event: asyncio.Event | None = None,
        subscriber_notifier: SubscriberLifecycleNotifier | None = None,
        upload_timeout_seconds: float = 600.0,
    ) -> None:
        self._database = database
        self._executor = executor
        self._queue = queue
        self._clock = clock
        self._max_attempts = max_attempts
        self._lease = timedelta(seconds=lease_seconds)
        self.wake_event = wake_event or asyncio.Event()
        self._subscriber_notifier = subscriber_notifier
        self._upload_timeout = upload_timeout_seconds

    async def claim(self, worker_id: str) -> UploadJob | None:
        now = self._clock()
        async with self._database.transaction() as repositories:
            recovery = await repositories.upload_jobs.recover_expired(now, self._max_attempts)
            reconciled = await repositories.singleflight.reconcile_all(now)
            job = await repositories.upload_jobs.claim(
                worker_id=worker_id,
                now=now,
                lease_expires_at=now + self._lease,
            )
        for artifact in recovery.terminal_artifacts:
            self._queue.release_owned(*artifact)
        await self._notify_if(reconciled > 0)
        return job

    async def heartbeat(self, job_id: int, worker_id: str) -> bool:
        async with self._database.transaction() as repositories:
            return await repositories.upload_jobs.heartbeat(
                job_id, worker_id, self._clock() + self._lease
            )

    async def process(self, job: DownloadJob | UploadJob, worker_id: str) -> None:
        if not isinstance(job, UploadJob):
            raise TypeError("upload worker received wrong job type")
        valid_path = False
        try:
            path = self._queue.validate_artifact(job.artifact_job_id, job.artifact_path)
            valid_path = True
            async with asyncio.timeout(self._upload_timeout):
                await self._executor.upload(
                    UploadRequest(
                        job.id,
                        job.download_job_id,
                        job.track_id,
                        job.quality_profile,
                        job.artifact_job_id,
                        path,
                        _artifact_metadata_from_upload(job),
                        job.artifact_fingerprint,
                    )
                )
            async with self._database.transaction() as repositories:
                succeeded = await repositories.upload_jobs.succeed(
                    job_id=job.id, worker_id=worker_id, now=self._clock()
                )
                reconciled = await repositories.singleflight.reconcile_download_job(
                    job.download_job_id, self._clock()
                )
            await self._notify_if(reconciled)
            if succeeded:
                self._queue.release_owned(job.artifact_job_id, job.artifact_path)
            else:
                status = await self._transition_failure(
                    job,
                    worker_id,
                    QueueErrorCode.UPLOAD_RETRYABLE.value,
                    retryable=True,
                )
                if status in {QueueJobStatus.CANCELLED, QueueJobStatus.FAILED}:
                    self._queue.release_owned(job.artifact_job_id, job.artifact_path)
        except asyncio.CancelledError:
            status = await self._transition_failure(
                job,
                worker_id,
                QueueErrorCode.UPLOAD_RETRYABLE.value,
                retryable=True,
            )
            if status in {QueueJobStatus.CANCELLED, QueueJobStatus.FAILED} and valid_path:
                self._queue.release_owned(job.artifact_job_id, job.artifact_path)
        except UploadRetryableError as exc:
            status = await self._transition_failure(
                job,
                worker_id,
                exc.code.value,
                retryable=True,
                retry_after_seconds=exc.retry_after_seconds,
            )
            if status in {QueueJobStatus.CANCELLED, QueueJobStatus.FAILED} and valid_path:
                self._queue.release_owned(job.artifact_job_id, job.artifact_path)
        except TimeoutError:
            status = await self._transition_failure(
                job,
                worker_id,
                QueueErrorCode.UPLOAD_RETRYABLE.value,
                retryable=True,
            )
            if status in {QueueJobStatus.CANCELLED, QueueJobStatus.FAILED} and valid_path:
                self._queue.release_owned(job.artifact_job_id, job.artifact_path)
        except UploadTerminalError as exc:
            await self._terminal(job, worker_id, exc.code.value, valid_path)
        except FileNotFoundError:
            await self._terminal(job, worker_id, QueueErrorCode.UPLOAD_ARTIFACT_MISSING.value, True)
        except ArtifactPathError:
            await self._terminal(
                job, worker_id, QueueErrorCode.UPLOAD_ARTIFACT_INVALID.value, False
            )
        except Exception:
            logger.error(
                "Unexpected upload worker failure",
                extra={"job_id": job.id, "job_type": "upload", "worker_id": worker_id},
            )
            await self._terminal(
                job, worker_id, QueueErrorCode.UPLOAD_WORKER_ERROR.value, valid_path
            )
        self.wake_event.set()

    async def _terminal(self, job: UploadJob, worker_id: str, code: str, release: bool) -> None:
        status = await self._transition_failure(job, worker_id, code, retryable=False)
        if status in {QueueJobStatus.CANCELLED, QueueJobStatus.FAILED} and release:
            self._queue.release_owned(job.artifact_job_id, job.artifact_path)

    async def _transition_failure(
        self,
        job: UploadJob,
        worker_id: str,
        code: str,
        *,
        retryable: bool,
        retry_after_seconds: float | None = None,
    ) -> QueueJobStatus | None:
        now = self._clock()
        async with self._database.transaction() as repositories:
            status = await repositories.upload_jobs.retry_or_fail(
                job_id=job.id,
                worker_id=worker_id,
                now=now,
                available_at=now
                + _retry_delay(job.attempt_count, retry_after_seconds=retry_after_seconds),
                error_code=code,
                max_attempts=self._max_attempts,
                retryable=retryable,
            )
            reconciled = await repositories.singleflight.reconcile_download_job(
                job.download_job_id, now
            )
        await self._notify_if(reconciled)
        return status

    async def _notify_if(self, changed: bool) -> None:
        if changed and self._subscriber_notifier is not None:
            await self._subscriber_notifier.notify_all()


@dataclass(slots=True)
class _WorkerSlot:
    worker_id: str
    task: asyncio.Task[None]
    retire: bool = False


class AsyncWorkerPool:
    def __init__(
        self,
        name: str,
        backend: WorkerBackend,
        *,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    ) -> None:
        self._name = name
        self._backend = backend
        self._poll_seconds = poll_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._slots: dict[int, _WorkerSlot] = {}
        self._active_jobs: dict[int, asyncio.Task[None]] = {}
        self._desired = 0
        self._next_id = 1
        self._accepting = False
        self._lock = asyncio.Lock()

    @property
    def desired(self) -> int:
        return self._desired

    @property
    def actual(self) -> int:
        return sum(not slot.task.done() for slot in self._slots.values())

    async def start(self, workers: int) -> None:
        self._accepting = True
        await self.resize(workers)

    async def resize(self, workers: int) -> None:
        async with self._lock:
            self._desired = workers
            self._prune()
            live = [slot for slot in self._slots.values() if not slot.task.done()]
            retiring = [slot for slot in live if slot.retire]
            while len(live) - len(retiring) < workers and retiring:
                slot = retiring.pop(0)
                slot.retire = False
            active = [slot for slot in live if not slot.retire]
            while len(active) < workers and self._accepting:
                number = self._next_id
                self._next_id += 1
                worker_id = f"{self._name}-{number}"
                task = asyncio.create_task(self._worker(number), name=worker_id)
                slot = _WorkerSlot(worker_id, task)
                self._slots[number] = slot
                active.append(slot)
            if len(active) > workers:
                for slot in sorted(active, key=lambda item: item.worker_id, reverse=True)[workers:]:
                    slot.retire = True
            self._backend.wake_event.set()

    async def cancel_job(self, job_id: int) -> None:
        task = self._active_jobs.get(job_id)
        if task is not None:
            task.cancel()

    async def stop(self, grace_seconds: float = DEFAULT_SHUTDOWN_SECONDS) -> None:
        self._accepting = False
        self._desired = 0
        for slot in self._slots.values():
            slot.retire = True
        self._backend.wake_event.set()
        tasks = [slot.task for slot in self._slots.values() if not slot.task.done()]
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=grace_seconds)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            if not task.cancelled():
                task.exception()
        self._prune()

    async def _worker(self, number: int) -> None:
        slot = self._slots[number]
        worker_id = slot.worker_id
        while self._accepting and not slot.retire:
            try:
                job = await self._backend.claim(worker_id)
                if job is None:
                    await self._wait_for_work()
                    continue
                operation = asyncio.create_task(
                    self._backend.process(job, worker_id),
                    name=f"{worker_id}-job-{job.id}",
                )
                self._active_jobs[job.id] = operation
                heartbeat = asyncio.create_task(
                    self._heartbeat(job.id, worker_id, operation),
                    name=f"{worker_id}-heartbeat-{job.id}",
                )
                try:
                    await operation
                finally:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
                    self._active_jobs.pop(job.id, None)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.error(
                    "Worker loop failure",
                    extra={"job_type": self._name, "worker_id": worker_id},
                )
                await asyncio.sleep(min(self._poll_seconds, 1.0))

    async def _heartbeat(self, job_id: int, worker_id: str, operation: asyncio.Task[None]) -> None:
        while not operation.done():
            await asyncio.sleep(self._heartbeat_seconds)
            if not operation.done() and not await self._backend.heartbeat(job_id, worker_id):
                operation.cancel()
                return

    async def _wait_for_work(self) -> None:
        try:
            await asyncio.wait_for(self._backend.wake_event.wait(), timeout=self._poll_seconds)
        except TimeoutError:
            pass
        finally:
            self._backend.wake_event.clear()

    def _prune(self) -> None:
        self._slots = {number: slot for number, slot in self._slots.items() if not slot.task.done()}


class QueueManager:
    """Single-instance manager; production startup waits for a real upload executor."""

    def __init__(
        self,
        settings: Settings,
        worker_settings: WorkerSettingsService,
        download_queue: DownloadQueueService,
        upload_queue: UploadQueueService,
        download_backend: DownloadWorkerBackend,
        upload_backend: UploadWorkerBackend | None,
        *,
        singleflight: SingleFlightService | None = None,
        reconcile_seconds: float = 1.0,
        download_lifecycle: DownloadLifecycleService | None = None,
    ) -> None:
        self._settings = settings
        self._worker_settings = worker_settings
        self._download_queue = download_queue
        self._upload_queue = upload_queue
        self._download_pool = AsyncWorkerPool("download", download_backend)
        self._upload_pool = (
            AsyncWorkerPool("upload", upload_backend) if upload_backend is not None else None
        )
        self._singleflight = singleflight
        self._reconcile_seconds = reconcile_seconds
        self._download_lifecycle = download_lifecycle
        self._reconciler: asyncio.Task[None] | None = None
        self._started = False
        worker_settings.attach_resizer(self)
        if singleflight is not None:
            singleflight.attach_operation_canceller(self)

    async def start(self) -> None:
        values = await self._worker_settings.initialize()
        if self._singleflight is not None:
            await self._singleflight.reconcile()
        self._started = True
        # Without a delivery backend, neither pool starts: this prevents artifact accumulation.
        if self._upload_pool is None:
            return
        await self._download_pool.start(values.download.current)
        await self._upload_pool.start(values.upload.current)
        self._reconciler = asyncio.create_task(self._reconcile(), name="queue-reconciler")

    async def stop(self, grace_seconds: float = DEFAULT_SHUTDOWN_SECONDS) -> None:
        self._started = False
        if self._reconciler is not None:
            self._reconciler.cancel()
            await asyncio.gather(self._reconciler, return_exceptions=True)
            self._reconciler = None
        await self._download_pool.stop(grace_seconds)
        if self._upload_pool is not None:
            await self._upload_pool.stop(grace_seconds)

    async def resize_download(self, workers: int) -> None:
        if self._started and self._upload_pool is not None:
            await self._download_pool.resize(workers)

    async def resize_upload(self, workers: int) -> None:
        if self._started and self._upload_pool is not None:
            await self._upload_pool.resize(workers)

    async def cancel_download(self, job_id: int) -> QueueJobStatus:
        status = await self._download_queue.cancel(job_id)
        if status is QueueJobStatus.RUNNING:
            await self._download_pool.cancel_job(job_id)
        return status

    async def cancel_upload(self, job_id: int) -> QueueJobStatus:
        status = await self._upload_queue.cancel(job_id)
        if status is QueueJobStatus.RUNNING and self._upload_pool is not None:
            await self._upload_pool.cancel_job(job_id)
        return status

    async def cancel_download_operation(self, job_id: int) -> None:
        await self._download_pool.cancel_job(job_id)

    async def cancel_upload_operation(self, job_id: int) -> None:
        if self._upload_pool is not None:
            await self._upload_pool.cancel_job(job_id)

    async def snapshot(self) -> QueueRuntimeSnapshot:
        values = await self._worker_settings.get_values()
        return QueueRuntimeSnapshot(
            download=WorkerPoolSnapshot(
                values.download.current,
                self._download_pool.actual,
                values.download.default,
                values.download.maximum,
            ),
            upload=WorkerPoolSnapshot(
                values.upload.current,
                self._upload_pool.actual if self._upload_pool is not None else 0,
                values.upload.default,
                values.upload.maximum,
            ),
            download_jobs=await self._download_queue.counts(),
            upload_jobs=await self._upload_queue.counts(),
            singleflight=(
                await self._singleflight.snapshot() if self._singleflight is not None else None
            ),
        )

    async def _reconcile(self) -> None:
        while self._started:
            try:
                values = await self._worker_settings.get_values()
                await self._download_pool.resize(values.download.current)
                if self._upload_pool is not None:
                    await self._upload_pool.resize(values.upload.current)
                if self._singleflight is not None:
                    await self._singleflight.reconcile()
                if self._download_lifecycle is not None:
                    await self._download_lifecycle.recover()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.error("Queue worker reconciliation failed")
            await asyncio.sleep(self._reconcile_seconds)


def _retry_delay(attempt_count: int, *, retry_after_seconds: float | None = None) -> timedelta:
    backoff = min(2 ** max(attempt_count - 1, 0), 30)
    return timedelta(seconds=max(backoff, retry_after_seconds or 0))


def _artifact_metadata(result: DownloadResult) -> DownloadArtifactMetadata:
    source = result.source_media
    output = result.output_media
    return DownloadArtifactMetadata(
        track_source_id=result.track_source_id,
        source_provider=result.provider,
        source_provider_track_id=result.provider_track_id,
        operation=result.operation,
        transcoded=result.transcoded,
        source_codec=source.codec,
        source_container=source.container,
        source_bitrate_kbps=source.bitrate_kbps,
        output_codec=output.codec,
        output_container=output.container,
        output_bitrate_kbps=output.bitrate_kbps,
        sample_rate_hz=output.sample_rate_hz,
        bit_depth=output.bit_depth,
        channels=output.channels,
        duration_ms=output.duration_ms,
        file_size_bytes=result.file_size,
        encoder=result.encoder,
    )


def _artifact_metadata_from_upload(job: UploadJob) -> DownloadArtifactMetadata | None:
    if (
        job.source_provider is None
        or job.source_provider_track_id is None
        or job.operation is None
        or job.transcoded is None
        or job.file_size_bytes is None
    ):
        return None
    return DownloadArtifactMetadata(
        track_source_id=job.source_track_source_id,
        source_provider=job.source_provider,
        source_provider_track_id=job.source_provider_track_id,
        operation=job.operation,
        transcoded=job.transcoded,
        source_codec=job.source_codec,
        source_container=job.source_container,
        source_bitrate_kbps=job.source_bitrate_kbps,
        output_codec=job.output_codec,
        output_container=job.output_container,
        output_bitrate_kbps=job.output_bitrate_kbps,
        sample_rate_hz=job.sample_rate_hz,
        bit_depth=job.bit_depth,
        channels=job.channels,
        duration_ms=job.duration_ms,
        file_size_bytes=job.file_size_bytes,
        encoder=job.encoder,
    )
