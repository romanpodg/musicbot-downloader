"""SQLite-backed persistent queue operations."""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import case, false, func, select, text, true, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import QualityProfile, QueueJobStatus
from app.core.exceptions import QueueFullError, TrackNotFound
from app.core.models import DownloadArtifactMetadata
from app.storage.models import DownloadJob, RuntimeSettings, Track, TrackSource, UploadJob

TERMINAL_STATUSES = (
    QueueJobStatus.SUCCEEDED,
    QueueJobStatus.FAILED,
    QueueJobStatus.CANCELLED,
)


@dataclass(frozen=True, slots=True)
class UploadLeaseRecovery:
    recovered: int
    terminal_artifacts: tuple[tuple[str, str], ...]


class DownloadJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def submit(
        self, *, track_id: int, quality_profile: QualityProfile, max_active: int, now: datetime
    ) -> DownloadJob:
        # SQLite's reserved write lock serializes the admission COUNT + INSERT pair.
        await self._session.execute(text("BEGIN IMMEDIATE"))
        if await self._session.get(Track, track_id) is None:
            raise TrackNotFound()
        active = await self._session.scalar(
            select(func.count(DownloadJob.id)).where(DownloadJob.status.not_in(TERMINAL_STATUSES))
        )
        if (active or 0) >= max_active:
            raise QueueFullError()
        job = DownloadJob(
            track_id=track_id,
            quality_profile=quality_profile,
            status=QueueJobStatus.QUEUED,
            queued_at=now,
            available_at=now,
        )
        self._session.add(job)
        await self._session.flush()
        return job

    async def get(self, job_id: int) -> DownloadJob | None:
        return await self._session.get(DownloadJob, job_id)

    async def list(self, *, offset: int, limit: int) -> list[DownloadJob]:
        rows = await self._session.scalars(
            select(DownloadJob).order_by(DownloadJob.id.desc()).offset(offset).limit(limit)
        )
        return list(rows)

    async def counts(self) -> dict[QueueJobStatus, int]:
        rows = await self._session.execute(
            select(DownloadJob.status, func.count(DownloadJob.id)).group_by(DownloadJob.status)
        )
        return {status: count for status, count in rows}

    async def recover_expired(self, now: datetime, max_attempts: int) -> int:
        cancelled = await self._session.execute(
            update(DownloadJob)
            .where(
                DownloadJob.status == QueueJobStatus.RUNNING,
                DownloadJob.lease_expires_at <= now,
                DownloadJob.cancel_requested.is_(True),
            )
            .values(
                status=QueueJobStatus.CANCELLED,
                finished_at=now,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        exhausted = await self._session.execute(
            update(DownloadJob)
            .where(
                DownloadJob.status == QueueJobStatus.RUNNING,
                DownloadJob.lease_expires_at <= now,
                DownloadJob.cancel_requested.is_(False),
                DownloadJob.attempt_count >= max_attempts,
            )
            .values(
                status=QueueJobStatus.FAILED,
                finished_at=now,
                last_error_code="DOWNLOAD_WORKER_ERROR",
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        requeued = await self._session.execute(
            update(DownloadJob)
            .where(
                DownloadJob.status == QueueJobStatus.RUNNING,
                DownloadJob.lease_expires_at <= now,
                DownloadJob.cancel_requested.is_(False),
                DownloadJob.attempt_count < max_attempts,
            )
            .values(
                status=QueueJobStatus.QUEUED,
                available_at=now,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        return _rowcount(cancelled) + _rowcount(exhausted) + _rowcount(requeued)

    async def claim(
        self, *, worker_id: str, now: datetime, lease_expires_at: datetime
    ) -> DownloadJob | None:
        candidate = (
            select(DownloadJob.id)
            .where(
                DownloadJob.status == QueueJobStatus.QUEUED,
                DownloadJob.available_at <= now,
                DownloadJob.cancel_requested.is_(False),
            )
            .order_by(DownloadJob.queued_at, DownloadJob.id)
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            update(DownloadJob)
            .where(
                DownloadJob.id == candidate,
                DownloadJob.status == QueueJobStatus.QUEUED,
            )
            .values(
                status=QueueJobStatus.RUNNING,
                attempt_count=DownloadJob.attempt_count + 1,
                started_at=now,
                finished_at=None,
                lease_owner=worker_id,
                lease_expires_at=lease_expires_at,
            )
            .returning(DownloadJob)
        )
        return (await self._session.scalars(statement)).one_or_none()

    async def heartbeat(self, job_id: int, worker_id: str, lease_expires_at: datetime) -> bool:
        result = await self._session.execute(
            update(DownloadJob)
            .where(
                DownloadJob.id == job_id,
                DownloadJob.status == QueueJobStatus.RUNNING,
                DownloadJob.lease_owner == worker_id,
            )
            .values(lease_expires_at=lease_expires_at)
        )
        return _changed(result)

    async def handoff(
        self,
        *,
        job_id: int,
        worker_id: str,
        artifact_job_id: str,
        artifact_path: str,
        now: datetime,
        artifact: DownloadArtifactMetadata | None = None,
    ) -> UploadJob | None:
        result = await self._session.execute(
            update(DownloadJob)
            .where(
                DownloadJob.id == job_id,
                DownloadJob.status == QueueJobStatus.RUNNING,
                DownloadJob.lease_owner == worker_id,
                DownloadJob.cancel_requested.is_(False),
            )
            .values(
                status=QueueJobStatus.SUCCEEDED,
                finished_at=now,
                artifact_job_id=artifact_job_id,
                artifact_path=artifact_path,
                last_error_code=None,
                last_error_detail=None,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        if not _changed(result):
            return None
        job = await self._session.get(DownloadJob, job_id)
        if job is None:
            return None
        source_track_source_id = artifact.track_source_id if artifact else None
        if (
            source_track_source_id is not None
            and await self._session.get(TrackSource, source_track_source_id) is None
        ):
            source_track_source_id = None
        upload = UploadJob(
            download_job_id=job.id,
            track_id=job.track_id,
            quality_profile=job.quality_profile,
            status=QueueJobStatus.QUEUED,
            artifact_job_id=artifact_job_id,
            artifact_path=artifact_path,
            source_track_source_id=source_track_source_id,
            source_provider=(artifact.source_provider if artifact else None),
            source_provider_track_id=(artifact.source_provider_track_id if artifact else None),
            operation=(artifact.operation if artifact else None),
            transcoded=(artifact.transcoded if artifact else None),
            source_codec=(artifact.source_codec if artifact else None),
            source_container=(artifact.source_container if artifact else None),
            source_bitrate_kbps=(artifact.source_bitrate_kbps if artifact else None),
            output_codec=(artifact.output_codec if artifact else None),
            output_container=(artifact.output_container if artifact else None),
            output_bitrate_kbps=(artifact.output_bitrate_kbps if artifact else None),
            sample_rate_hz=(artifact.sample_rate_hz if artifact else None),
            bit_depth=(artifact.bit_depth if artifact else None),
            channels=(artifact.channels if artifact else None),
            duration_ms=(artifact.duration_ms if artifact else None),
            file_size_bytes=(artifact.file_size_bytes if artifact else None),
            encoder=(artifact.encoder if artifact else None),
            queued_at=now,
            available_at=now,
        )
        self._session.add(upload)
        await self._session.flush()
        return upload

    async def retry_or_fail(
        self,
        *,
        job_id: int,
        worker_id: str,
        now: datetime,
        available_at: datetime,
        error_code: str,
        max_attempts: int,
    ) -> QueueJobStatus | None:
        terminal = await self._session.execute(
            update(DownloadJob)
            .where(
                DownloadJob.id == job_id,
                DownloadJob.status == QueueJobStatus.RUNNING,
                DownloadJob.lease_owner == worker_id,
                (DownloadJob.cancel_requested.is_(True))
                | (DownloadJob.attempt_count >= max_attempts),
            )
            .values(
                status=case(
                    (DownloadJob.cancel_requested.is_(True), QueueJobStatus.CANCELLED),
                    else_=QueueJobStatus.FAILED,
                ),
                finished_at=now,
                last_error_code=error_code,
                last_error_detail=None,
                lease_owner=None,
                lease_expires_at=None,
            )
            .returning(DownloadJob.status)
        )
        terminal_status = terminal.scalar_one_or_none()
        if terminal_status is not None:
            return terminal_status
        retry = await self._session.execute(
            update(DownloadJob)
            .where(
                DownloadJob.id == job_id,
                DownloadJob.status == QueueJobStatus.RUNNING,
                DownloadJob.lease_owner == worker_id,
                DownloadJob.cancel_requested.is_(False),
                DownloadJob.attempt_count < max_attempts,
            )
            .values(
                status=QueueJobStatus.QUEUED,
                available_at=available_at,
                finished_at=None,
                last_error_code=error_code,
                last_error_detail=None,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        return QueueJobStatus.QUEUED if _changed(retry) else None

    async def fail(self, *, job_id: int, worker_id: str, now: datetime, error_code: str) -> bool:
        result = await self._session.execute(
            update(DownloadJob)
            .where(
                DownloadJob.id == job_id,
                DownloadJob.status == QueueJobStatus.RUNNING,
                DownloadJob.lease_owner == worker_id,
            )
            .values(
                status=case(
                    (DownloadJob.cancel_requested.is_(True), QueueJobStatus.CANCELLED),
                    else_=QueueJobStatus.FAILED,
                ),
                finished_at=now,
                last_error_code=error_code,
                last_error_detail=None,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        return _changed(result)

    async def cancel(self, job_id: int, now: datetime) -> QueueJobStatus | None:
        queued = await self._session.execute(
            update(DownloadJob)
            .where(DownloadJob.id == job_id, DownloadJob.status == QueueJobStatus.QUEUED)
            .values(
                status=QueueJobStatus.CANCELLED,
                cancel_requested=True,
                finished_at=now,
            )
        )
        if _changed(queued):
            return QueueJobStatus.CANCELLED
        running = await self._session.execute(
            update(DownloadJob)
            .where(DownloadJob.id == job_id, DownloadJob.status == QueueJobStatus.RUNNING)
            .values(cancel_requested=True)
        )
        if _changed(running):
            return QueueJobStatus.RUNNING
        job = await self._session.get(DownloadJob, job_id)
        return job.status if job is not None else None


class UploadJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, job_id: int) -> UploadJob | None:
        return await self._session.get(UploadJob, job_id)

    async def list(self, *, offset: int, limit: int) -> list[UploadJob]:
        rows = await self._session.scalars(
            select(UploadJob).order_by(UploadJob.id.desc()).offset(offset).limit(limit)
        )
        return list(rows)

    async def counts(self) -> dict[QueueJobStatus, int]:
        rows = await self._session.execute(
            select(UploadJob.status, func.count(UploadJob.id)).group_by(UploadJob.status)
        )
        return {status: count for status, count in rows}

    async def recover_expired(self, now: datetime, max_attempts: int) -> UploadLeaseRecovery:
        cancelled = await self._session.execute(
            update(UploadJob)
            .where(
                UploadJob.status == QueueJobStatus.RUNNING,
                UploadJob.lease_expires_at <= now,
                UploadJob.cancel_requested.is_(True),
            )
            .values(
                status=QueueJobStatus.CANCELLED,
                finished_at=now,
                lease_owner=None,
                lease_expires_at=None,
            )
            .returning(UploadJob.artifact_job_id, UploadJob.artifact_path)
        )
        released = builtins.list(cancelled.tuples())
        exhausted = await self._session.execute(
            update(UploadJob)
            .where(
                UploadJob.status == QueueJobStatus.RUNNING,
                UploadJob.lease_expires_at <= now,
                UploadJob.cancel_requested.is_(False),
                UploadJob.attempt_count >= max_attempts,
            )
            .values(
                status=QueueJobStatus.FAILED,
                finished_at=now,
                last_error_code="UPLOAD_WORKER_ERROR",
                lease_owner=None,
                lease_expires_at=None,
            )
            .returning(UploadJob.artifact_job_id, UploadJob.artifact_path)
        )
        released.extend(exhausted.tuples())
        requeued = await self._session.execute(
            update(UploadJob)
            .where(
                UploadJob.status == QueueJobStatus.RUNNING,
                UploadJob.lease_expires_at <= now,
                UploadJob.cancel_requested.is_(False),
                UploadJob.attempt_count < max_attempts,
            )
            .values(
                status=QueueJobStatus.QUEUED,
                available_at=now,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        return UploadLeaseRecovery(len(released) + _rowcount(requeued), tuple(released))

    async def list_nonterminal(self) -> builtins.list[UploadJob]:
        rows = await self._session.scalars(
            select(UploadJob)
            .where(UploadJob.status.not_in(TERMINAL_STATUSES))
            .order_by(UploadJob.id)
        )
        return builtins.list(rows)

    async def protected_artifact_job_ids(self) -> set[str]:
        rows = await self._session.scalars(
            select(UploadJob.artifact_job_id).where(UploadJob.status.not_in(TERMINAL_STATUSES))
        )
        return set(rows)

    async def fail_recovered(self, *, job_id: int, now: datetime, error_code: str) -> bool:
        result = await self._session.execute(
            update(UploadJob)
            .where(UploadJob.id == job_id, UploadJob.status.not_in(TERMINAL_STATUSES))
            .values(
                status=case(
                    (UploadJob.cancel_requested.is_(True), QueueJobStatus.CANCELLED),
                    else_=QueueJobStatus.FAILED,
                ),
                finished_at=now,
                last_error_code=error_code,
                last_error_detail=None,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        return _changed(result)

    async def succeed_recovered(self, *, job_id: int, now: datetime) -> bool:
        result = await self._session.execute(
            update(UploadJob)
            .where(
                UploadJob.id == job_id,
                UploadJob.status.not_in(TERMINAL_STATUSES),
                UploadJob.cancel_requested.is_(False),
            )
            .values(
                status=QueueJobStatus.SUCCEEDED,
                finished_at=now,
                last_error_code=None,
                last_error_detail=None,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        return _changed(result)

    async def claim(
        self, *, worker_id: str, now: datetime, lease_expires_at: datetime
    ) -> UploadJob | None:
        candidate = (
            select(UploadJob.id)
            .where(
                UploadJob.status == QueueJobStatus.QUEUED,
                UploadJob.available_at <= now,
                UploadJob.cancel_requested.is_(False),
            )
            .order_by(UploadJob.queued_at, UploadJob.id)
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            update(UploadJob)
            .where(UploadJob.id == candidate, UploadJob.status == QueueJobStatus.QUEUED)
            .values(
                status=QueueJobStatus.RUNNING,
                attempt_count=UploadJob.attempt_count + 1,
                started_at=now,
                finished_at=None,
                lease_owner=worker_id,
                lease_expires_at=lease_expires_at,
            )
            .returning(UploadJob)
        )
        return (await self._session.scalars(statement)).one_or_none()

    async def heartbeat(self, job_id: int, worker_id: str, lease_expires_at: datetime) -> bool:
        result = await self._session.execute(
            update(UploadJob)
            .where(
                UploadJob.id == job_id,
                UploadJob.status == QueueJobStatus.RUNNING,
                UploadJob.lease_owner == worker_id,
            )
            .values(lease_expires_at=lease_expires_at)
        )
        return _changed(result)

    async def succeed(self, *, job_id: int, worker_id: str, now: datetime) -> bool:
        result = await self._session.execute(
            update(UploadJob)
            .where(
                UploadJob.id == job_id,
                UploadJob.status == QueueJobStatus.RUNNING,
                UploadJob.lease_owner == worker_id,
                UploadJob.cancel_requested.is_(False),
            )
            .values(
                status=QueueJobStatus.SUCCEEDED,
                finished_at=now,
                last_error_code=None,
                last_error_detail=None,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        return _changed(result)

    async def retry_or_fail(
        self,
        *,
        job_id: int,
        worker_id: str,
        now: datetime,
        available_at: datetime,
        error_code: str,
        max_attempts: int,
        retryable: bool,
    ) -> QueueJobStatus | None:
        terminal_condition = (
            true()
            if not retryable
            else (UploadJob.cancel_requested.is_(True)) | (UploadJob.attempt_count >= max_attempts)
        )
        terminal = await self._session.execute(
            update(UploadJob)
            .where(
                UploadJob.id == job_id,
                UploadJob.status == QueueJobStatus.RUNNING,
                UploadJob.lease_owner == worker_id,
                terminal_condition,
            )
            .values(
                status=case(
                    (UploadJob.cancel_requested.is_(True), QueueJobStatus.CANCELLED),
                    else_=QueueJobStatus.FAILED,
                ),
                finished_at=now,
                last_error_code=error_code,
                last_error_detail=None,
                lease_owner=None,
                lease_expires_at=None,
            )
            .returning(UploadJob.status)
        )
        terminal_status = terminal.scalar_one_or_none()
        if terminal_status is not None:
            return terminal_status
        retry = await self._session.execute(
            update(UploadJob)
            .where(
                UploadJob.id == job_id,
                UploadJob.status == QueueJobStatus.RUNNING,
                UploadJob.lease_owner == worker_id,
                UploadJob.cancel_requested.is_(False),
                UploadJob.attempt_count < max_attempts,
                false() if not retryable else true(),
            )
            .values(
                status=QueueJobStatus.QUEUED,
                available_at=available_at,
                finished_at=None,
                last_error_code=error_code,
                last_error_detail=None,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        return QueueJobStatus.QUEUED if _changed(retry) else None

    async def cancel(self, job_id: int, now: datetime) -> QueueJobStatus | None:
        queued = await self._session.execute(
            update(UploadJob)
            .where(UploadJob.id == job_id, UploadJob.status == QueueJobStatus.QUEUED)
            .values(
                status=QueueJobStatus.CANCELLED,
                cancel_requested=True,
                finished_at=now,
            )
        )
        if _changed(queued):
            return QueueJobStatus.CANCELLED
        running = await self._session.execute(
            update(UploadJob)
            .where(UploadJob.id == job_id, UploadJob.status == QueueJobStatus.RUNNING)
            .values(cancel_requested=True)
        )
        if _changed(running):
            return QueueJobStatus.RUNNING
        job = await self._session.get(UploadJob, job_id)
        return job.status if job is not None else None


class RuntimeSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def initialize(
        self, *, download_default: int, upload_default: int, download_max: int, upload_max: int
    ) -> RuntimeSettings:
        await self._session.execute(
            sqlite_insert(RuntimeSettings)
            .values(id=1, download_workers=download_default, upload_workers=upload_default)
            .on_conflict_do_nothing(index_elements=[RuntimeSettings.id])
        )
        await self._session.execute(
            update(RuntimeSettings)
            .where(RuntimeSettings.id == 1)
            .values(
                download_workers=func.min(RuntimeSettings.download_workers, download_max),
                upload_workers=func.min(RuntimeSettings.upload_workers, upload_max),
            )
        )
        settings = await self._session.get(RuntimeSettings, 1)
        if settings is None:
            raise RuntimeError("runtime settings initialization failed")
        return settings

    async def get(self) -> RuntimeSettings | None:
        return await self._session.get(RuntimeSettings, 1)

    async def set_download_workers(self, value: int) -> None:
        await self._session.execute(
            update(RuntimeSettings).where(RuntimeSettings.id == 1).values(download_workers=value)
        )

    async def set_upload_workers(self, value: int) -> None:
        await self._session.execute(
            update(RuntimeSettings).where(RuntimeSettings.id == 1).values(upload_workers=value)
        )

    async def adjust_download_workers(
        self, delta: int, *, minimum: int, maximum: int
    ) -> tuple[int, int]:
        # A reserved SQLite write lock makes read/validate/write one serialized operation.
        await self._session.execute(text("BEGIN IMMEDIATE"))
        settings = await self._required_settings()
        previous = settings.download_workers
        current = previous + delta
        if current < minimum or current > maximum:
            return previous, previous
        await self._session.execute(
            update(RuntimeSettings).where(RuntimeSettings.id == 1).values(download_workers=current)
        )
        return previous, current

    async def adjust_upload_workers(
        self, delta: int, *, minimum: int, maximum: int
    ) -> tuple[int, int]:
        # Keep Download and Upload updates independent while retaining SQLite serialization.
        await self._session.execute(text("BEGIN IMMEDIATE"))
        settings = await self._required_settings()
        previous = settings.upload_workers
        current = previous + delta
        if current < minimum or current > maximum:
            return previous, previous
        await self._session.execute(
            update(RuntimeSettings).where(RuntimeSettings.id == 1).values(upload_workers=current)
        )
        return previous, current

    async def _required_settings(self) -> RuntimeSettings:
        settings = await self._session.get(RuntimeSettings, 1)
        if settings is None:
            raise RuntimeError("runtime settings are not initialized")
        return settings


def _changed(result: Any) -> bool:
    return bool(_rowcount(result))


def _rowcount(result: Any) -> int:
    return max(0, cast(CursorResult[Any], result).rowcount)
