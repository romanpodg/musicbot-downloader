"""Persistent SingleFlight admission and subscriber lifecycle operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import QualityProfile, QueueErrorCode, QueueJobStatus, SubscriberStatus
from app.core.exceptions import QueueFullError, TrackNotFound
from app.storage.models import (
    DownloadFlight,
    DownloadJob,
    JobSubscriber,
    Track,
    UploadJob,
)

TERMINAL_QUEUE_STATUSES = (
    QueueJobStatus.SUCCEEDED,
    QueueJobStatus.FAILED,
    QueueJobStatus.CANCELLED,
)


@dataclass(frozen=True, slots=True)
class AdmissionRecord:
    download_job: DownloadJob
    subscriber: JobSubscriber
    created_new_job: bool
    returned_existing_subscriber: bool
    reconciled_terminal_state: bool


@dataclass(frozen=True, slots=True)
class SubscriberRecord:
    subscriber: JobSubscriber
    download_job: DownloadJob


@dataclass(frozen=True, slots=True)
class CancellationRecord:
    subscriber: JobSubscriber
    download_job: DownloadJob
    cancel_download_operation: int | None = None
    cancel_upload_operation: int | None = None
    release_artifact: tuple[str, str] | None = None


class SingleFlightRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def submit(
        self,
        *,
        track_id: int,
        quality_profile: QualityProfile,
        request_key: str | None,
        max_active: int,
        now: datetime,
        acquire_lock: bool = True,
    ) -> AdmissionRecord:
        # The reserved SQLite write lock serializes lookup, reconciliation, capacity,
        # job/flight creation, and subscriber attachment in one short transaction.
        if acquire_lock:
            await self._session.execute(text("BEGIN IMMEDIATE"))
        if await self._session.get(Track, track_id) is None:
            raise TrackNotFound()

        reconciled = False
        flight = await self._flight_for_key(track_id, quality_profile)
        if flight is not None:
            eligible = await self._reconcile_flight(flight, now)
            reconciled = not eligible
            if eligible:
                job = await self._session.get(DownloadJob, flight.download_job_id)
                if job is None:  # Defensive; _reconcile_flight handles this case.
                    raise RuntimeError("eligible flight has no download job")
                subscriber, existing = await self._attach(job.id, request_key, now)
                return AdmissionRecord(job, subscriber, False, existing, reconciled)
            await self._session.flush()

        active = await self._session.scalar(
            select(func.count(DownloadJob.id)).where(
                DownloadJob.status.not_in(TERMINAL_QUEUE_STATUSES)
            )
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
        self._session.add(
            DownloadFlight(
                track_id=track_id,
                quality_profile=quality_profile,
                download_job_id=job.id,
            )
        )
        subscriber, _ = await self._attach(job.id, request_key, now)
        await self._session.flush()
        return AdmissionRecord(job, subscriber, True, False, reconciled)

    async def get_subscriber(self, subscriber_id: str) -> SubscriberRecord | None:
        row = (
            await self._session.execute(
                select(JobSubscriber, DownloadJob)
                .join(DownloadJob, DownloadJob.id == JobSubscriber.download_job_id)
                .where(JobSubscriber.id == subscriber_id)
            )
        ).one_or_none()
        return SubscriberRecord(*row) if row is not None else None

    async def list_job_subscribers(
        self, download_job_id: int, *, offset: int, limit: int
    ) -> list[SubscriberRecord]:
        rows = await self._session.execute(
            select(JobSubscriber, DownloadJob)
            .join(DownloadJob, DownloadJob.id == JobSubscriber.download_job_id)
            .where(JobSubscriber.download_job_id == download_job_id)
            .order_by(JobSubscriber.created_at, JobSubscriber.id)
            .offset(offset)
            .limit(limit)
        )
        return [SubscriberRecord(*row) for row in rows]

    async def subscriber_counts(
        self, download_job_id: int | None = None
    ) -> dict[SubscriberStatus, int]:
        statement = select(JobSubscriber.status, func.count(JobSubscriber.id)).group_by(
            JobSubscriber.status
        )
        if download_job_id is not None:
            statement = statement.where(JobSubscriber.download_job_id == download_job_id)
        rows = await self._session.execute(statement)
        return {status: count for status, count in rows}

    async def active_flight_count(self) -> int:
        return int(await self._session.scalar(select(func.count(DownloadFlight.id))) or 0)

    async def count_reconciliation_candidates(self) -> int:
        statement = (
            select(func.count(DownloadFlight.id))
            .select_from(DownloadFlight)
            .join(DownloadJob, DownloadJob.id == DownloadFlight.download_job_id)
            .outerjoin(UploadJob, UploadJob.download_job_id == DownloadJob.id)
            .where(
                or_(
                    DownloadJob.cancel_requested.is_(True),
                    DownloadJob.status.in_((QueueJobStatus.FAILED, QueueJobStatus.CANCELLED)),
                    and_(
                        DownloadJob.status == QueueJobStatus.SUCCEEDED,
                        or_(
                            UploadJob.id.is_(None),
                            UploadJob.cancel_requested.is_(True),
                            UploadJob.status.in_(TERMINAL_QUEUE_STATUSES),
                        ),
                    ),
                )
            )
        )
        return int(await self._session.scalar(statement) or 0)

    async def cancel_subscriber(
        self, subscriber_id: str, now: datetime
    ) -> CancellationRecord | None:
        await self._session.execute(text("BEGIN IMMEDIATE"))
        record = await self.get_subscriber(subscriber_id)
        if record is None:
            return None
        subscriber = record.subscriber
        job = record.download_job
        if subscriber.status is not SubscriberStatus.WAITING:
            return CancellationRecord(subscriber, job)

        subscriber.status = SubscriberStatus.CANCELLED
        subscriber.completed_at = now
        subscriber.last_error_code = None
        subscriber.updated_at = now
        await self._session.flush()
        waiting = await self._session.scalar(
            select(func.count(JobSubscriber.id)).where(
                JobSubscriber.download_job_id == job.id,
                JobSubscriber.status == SubscriberStatus.WAITING,
            )
        )
        if waiting:
            return CancellationRecord(subscriber, job)

        await self._session.execute(
            delete(DownloadFlight).where(DownloadFlight.download_job_id == job.id)
        )
        if job.status is QueueJobStatus.QUEUED:
            job.status = QueueJobStatus.CANCELLED
            job.cancel_requested = True
            job.finished_at = now
            return CancellationRecord(subscriber, job)
        if job.status is QueueJobStatus.RUNNING:
            job.cancel_requested = True
            return CancellationRecord(subscriber, job, cancel_download_operation=job.id)
        if job.status is not QueueJobStatus.SUCCEEDED:
            return CancellationRecord(subscriber, job)

        upload = await self._upload_for_download(job.id)
        if upload is None:
            return CancellationRecord(subscriber, job)
        if upload.status is QueueJobStatus.QUEUED:
            upload.status = QueueJobStatus.CANCELLED
            upload.cancel_requested = True
            upload.finished_at = now
            return CancellationRecord(
                subscriber,
                job,
                release_artifact=(upload.artifact_job_id, upload.artifact_path),
            )
        if upload.status is QueueJobStatus.RUNNING:
            upload.cancel_requested = True
            return CancellationRecord(subscriber, job, cancel_upload_operation=upload.id)
        return CancellationRecord(subscriber, job)

    async def reconcile_download_job(self, download_job_id: int, now: datetime) -> bool:
        flight = await self._session.scalar(
            select(DownloadFlight).where(DownloadFlight.download_job_id == download_job_id)
        )
        if flight is None:
            return False
        return not await self._reconcile_flight(flight, now)

    async def reconcile_all(self, now: datetime) -> int:
        flights = list(await self._session.scalars(select(DownloadFlight)))
        closed = 0
        for flight in flights:
            if not await self._reconcile_flight(flight, now):
                closed += 1
        return closed

    async def _flight_for_key(
        self, track_id: int, quality_profile: QualityProfile
    ) -> DownloadFlight | None:
        return (
            await self._session.scalars(
                select(DownloadFlight).where(
                    DownloadFlight.track_id == track_id,
                    DownloadFlight.quality_profile == quality_profile,
                )
            )
        ).one_or_none()

    async def _attach(
        self, download_job_id: int, request_key: str | None, now: datetime
    ) -> tuple[JobSubscriber, bool]:
        if request_key is not None:
            existing = await self._session.scalar(
                select(JobSubscriber).where(
                    JobSubscriber.download_job_id == download_job_id,
                    JobSubscriber.request_key == request_key,
                )
            )
            if existing is not None:
                return existing, True
        subscriber = JobSubscriber(
            id=str(uuid4()),
            download_job_id=download_job_id,
            status=SubscriberStatus.WAITING,
            request_key=request_key,
            created_at=now,
            updated_at=now,
        )
        self._session.add(subscriber)
        await self._session.flush()
        return subscriber, False

    async def _reconcile_flight(self, flight: DownloadFlight, now: datetime) -> bool:
        job = await self._session.get(DownloadJob, flight.download_job_id)
        target: SubscriberStatus | None = None
        error_code: str | None = None
        if job is None:
            target = SubscriberStatus.FAILED
            error_code = QueueErrorCode.INVALID_JOB_STATE.value
        elif job.cancel_requested or job.status is QueueJobStatus.CANCELLED:
            target = SubscriberStatus.CANCELLED
        elif job.status is QueueJobStatus.FAILED:
            target = SubscriberStatus.FAILED
            error_code = job.last_error_code
        elif job.status in {QueueJobStatus.QUEUED, QueueJobStatus.RUNNING}:
            return True
        elif job.status is QueueJobStatus.SUCCEEDED:
            upload = await self._upload_for_download(job.id)
            if upload is None:
                target = SubscriberStatus.FAILED
                error_code = QueueErrorCode.INVALID_JOB_STATE.value
            elif upload.cancel_requested or upload.status is QueueJobStatus.CANCELLED:
                target = SubscriberStatus.CANCELLED
            elif upload.status is QueueJobStatus.FAILED:
                target = SubscriberStatus.FAILED
                error_code = upload.last_error_code
            elif upload.status is QueueJobStatus.SUCCEEDED:
                target = SubscriberStatus.READY
            else:
                return True

        await self._session.execute(
            update(JobSubscriber)
            .where(
                JobSubscriber.download_job_id == flight.download_job_id,
                JobSubscriber.status == SubscriberStatus.WAITING,
            )
            .values(
                status=target,
                completed_at=now,
                updated_at=now,
                last_error_code=error_code if target is SubscriberStatus.FAILED else None,
            )
        )
        await self._session.delete(flight)
        return False

    async def _upload_for_download(self, download_job_id: int) -> UploadJob | None:
        return (
            await self._session.scalars(
                select(UploadJob).where(UploadJob.download_job_id == download_job_id)
            )
        ).one_or_none()
