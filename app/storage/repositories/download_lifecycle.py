"""Atomic Stage 21 lifecycle operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import case, or_, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.delivery_targets import DeliveryTargetType
from app.core.download_preferences import EffectiveDownloadProfile
from app.core.enums import DownloadDeliveryStatus, DownloadJobStatus, DownloadPhase
from app.storage.models.download_lifecycle import (
    DownloadDelivery,
    DownloadLifecycleJob,
    DownloadRequestRecord,
)


class DownloadLifecycleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def admit(
        self,
        *,
        requester_user_id: int,
        confirmation_id: str,
        source_type: str,
        source_reference: str,
        provider: str | None,
        provider_media_id: str | None,
        delivery_target_type: DeliveryTargetType,
        delivery_target_id: int,
        now: datetime,
        max_attempts: int = 3,
        initial_status: DownloadJobStatus = DownloadJobStatus.QUEUED,
        telegram_delivery_request_id: int | None = None,
        profile: EffectiveDownloadProfile | None = None,
    ) -> tuple[DownloadRequestRecord, DownloadLifecycleJob, DownloadDelivery]:
        # SQLite serializes this short admission transaction.  The unique
        # confirmation/request constraints remain the final race invariant.
        await self._session.execute(text("BEGIN IMMEDIATE"))
        await self._session.execute(
            sqlite_insert(DownloadRequestRecord)
            .values(
                requester_user_id=requester_user_id,
                confirmation_id=confirmation_id,
                source_type=source_type,
                source_reference=source_reference,
                provider=provider,
                provider_media_id=provider_media_id,
                delivery_target_type=delivery_target_type,
                delivery_target_id=delivery_target_id,
                created_at=now,
                updated_at=now,
                requested_quality=(profile.requested_quality if profile else None),
                effective_quality=(profile.effective_quality if profile else None),
                requested_format=(profile.requested_format if profile else None),
                effective_format=(profile.effective_format if profile else None),
                delivery_mode=(profile.delivery_mode if profile else None),
                embed_metadata=(profile.embed_metadata if profile else None),
                embed_cover=(profile.embed_cover if profile else None),
                profile_fallback_applied=(profile.fallback_applied if profile else None),
                profile_fallback_reason=(
                    profile.fallback_reason.value if profile and profile.fallback_reason else None
                ),
            )
            .on_conflict_do_nothing(index_elements=[DownloadRequestRecord.confirmation_id])
        )
        request = await self._session.scalar(
            select(DownloadRequestRecord).where(
                DownloadRequestRecord.confirmation_id == confirmation_id
            )
        )
        if request is None:
            raise RuntimeError("download request admission failed")
        await self._session.execute(
            sqlite_insert(DownloadLifecycleJob)
            .values(
                request_id=request.id,
                status=initial_status,
                attempt=0,
                max_attempts=max_attempts,
                queued_at=now,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[DownloadLifecycleJob.request_id])
        )
        job = await self._session.scalar(
            select(DownloadLifecycleJob).where(DownloadLifecycleJob.request_id == request.id)
        )
        if job is None:
            raise RuntimeError("download job admission failed")
        await self._session.execute(
            sqlite_insert(DownloadDelivery)
            .values(
                job_id=job.id,
                telegram_delivery_request_id=telegram_delivery_request_id,
                target_type=request.delivery_target_type,
                target_id=request.delivery_target_id,
                status=DownloadDeliveryStatus.PENDING,
                attempt=0,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[DownloadDelivery.job_id])
        )
        delivery = await self._session.scalar(
            select(DownloadDelivery).where(DownloadDelivery.job_id == job.id)
        )
        if delivery is None:
            raise RuntimeError("download delivery admission failed")
        return request, job, delivery

    async def get_by_confirmation(self, confirmation_id: str) -> DownloadRequestRecord | None:
        return cast(
            DownloadRequestRecord | None,
            await self._session.scalar(
                select(DownloadRequestRecord).where(
                    DownloadRequestRecord.confirmation_id == confirmation_id
                )
            ),
        )

    async def get_request(self, request_id: int) -> DownloadRequestRecord | None:
        return await self._session.get(DownloadRequestRecord, request_id)

    async def get_job(self, job_id: int) -> DownloadLifecycleJob | None:
        return await self._session.get(DownloadLifecycleJob, job_id)

    async def get_delivery(self, job_id: int) -> DownloadDelivery | None:
        return cast(
            DownloadDelivery | None,
            await self._session.scalar(
                select(DownloadDelivery).where(DownloadDelivery.job_id == job_id)
            ),
        )

    async def get_by_telegram_request(
        self, request_id: int
    ) -> tuple[DownloadLifecycleJob, DownloadDelivery] | None:
        row = await self._session.execute(
            select(DownloadLifecycleJob, DownloadDelivery)
            .join(DownloadDelivery, DownloadDelivery.job_id == DownloadLifecycleJob.id)
            .where(DownloadDelivery.telegram_delivery_request_id == request_id)
        )
        value = row.one_or_none()
        return cast(tuple[DownloadLifecycleJob, DownloadDelivery] | None, value)

    async def reconcile_telegram(
        self, request_id: int, status: str, error_code: str | None, now: datetime
    ) -> bool:
        pair = await self.get_by_telegram_request(request_id)
        if pair is None:
            return False
        job, delivery = pair
        if status == "DELIVERED":
            await self._session.execute(
                update(DownloadDelivery)
                .where(DownloadDelivery.id == delivery.id)
                .values(status=DownloadDeliveryStatus.DELIVERED, delivered_at=now)
            )
            result = await self._session.execute(
                update(DownloadLifecycleJob)
                .where(
                    DownloadLifecycleJob.id == job.id,
                    DownloadLifecycleJob.status.not_in(
                        (
                            DownloadJobStatus.SUCCEEDED,
                            DownloadJobStatus.FAILED,
                            DownloadJobStatus.CANCELLED,
                        )
                    ),
                )
                .values(
                    status=DownloadJobStatus.SUCCEEDED,
                    finished_at=now,
                    phase=None,
                    lease_owner=None,
                    lease_expires_at=None,
                )
            )
            return _changed(result)
        if status == "CANCELLED":
            target = DownloadJobStatus.CANCELLED
        elif status == "FAILED":
            target = DownloadJobStatus.FAILED
        elif status == "SENDING":
            target = DownloadJobStatus.DELIVERING
        elif status == "WAITING":
            target = DownloadJobStatus.RUNNING
        elif status == "QUEUED":
            target = DownloadJobStatus.QUEUED
        else:
            return False
        values: dict[str, object] = {"status": target, "error_code": error_code}
        if target in (DownloadJobStatus.FAILED, DownloadJobStatus.CANCELLED):
            values["finished_at"] = now
        result = await self._session.execute(
            update(DownloadLifecycleJob)
            .where(
                DownloadLifecycleJob.id == job.id,
                DownloadLifecycleJob.status.not_in(
                    (
                        DownloadJobStatus.SUCCEEDED,
                        DownloadJobStatus.FAILED,
                        DownloadJobStatus.CANCELLED,
                    )
                ),
            )
            .values(**values)
        )
        return _changed(result)

    async def queue(self, job_id: int, now: datetime) -> bool:
        result = await self._session.execute(
            update(DownloadLifecycleJob)
            .where(
                DownloadLifecycleJob.id == job_id,
                DownloadLifecycleJob.status.in_(
                    (DownloadJobStatus.PENDING, DownloadJobStatus.RETRY_WAIT)
                ),
                or_(DownloadLifecycleJob.retry_at.is_(None), DownloadLifecycleJob.retry_at <= now),
            )
            .values(status=DownloadJobStatus.QUEUED, queued_at=now, retry_at=None, phase=None)
        )
        return _changed(result)

    async def claim(
        self, *, worker_id: str, now: datetime, lease_expires_at: datetime
    ) -> DownloadLifecycleJob | None:
        candidate = (
            select(DownloadLifecycleJob.id)
            .where(
                DownloadLifecycleJob.status == DownloadJobStatus.QUEUED,
                or_(DownloadLifecycleJob.retry_at.is_(None), DownloadLifecycleJob.retry_at <= now),
                DownloadLifecycleJob.cancel_requested_at.is_(None),
            )
            .order_by(DownloadLifecycleJob.queued_at, DownloadLifecycleJob.id)
            .limit(1)
            .scalar_subquery()
        )
        result = await self._session.execute(
            update(DownloadLifecycleJob)
            .where(
                DownloadLifecycleJob.id == candidate,
                DownloadLifecycleJob.status == DownloadJobStatus.QUEUED,
            )
            .values(
                status=DownloadJobStatus.RUNNING,
                attempt=DownloadLifecycleJob.attempt + 1,
                started_at=now,
                lease_owner=worker_id,
                lease_expires_at=lease_expires_at,
                phase=DownloadPhase.RESOLVING,
            )
            .returning(DownloadLifecycleJob)
        )
        return result.scalar_one_or_none()

    async def heartbeat(self, job_id: int, worker_id: str, lease_expires_at: datetime) -> bool:
        result = await self._session.execute(
            update(DownloadLifecycleJob)
            .where(
                DownloadLifecycleJob.id == job_id,
                DownloadLifecycleJob.status.in_(
                    (DownloadJobStatus.RUNNING, DownloadJobStatus.DELIVERING)
                ),
                DownloadLifecycleJob.lease_owner == worker_id,
            )
            .values(lease_expires_at=lease_expires_at)
        )
        return _changed(result)

    async def set_phase(self, job_id: int, worker_id: str, phase: DownloadPhase) -> bool:
        result = await self._session.execute(
            update(DownloadLifecycleJob)
            .where(
                DownloadLifecycleJob.id == job_id,
                DownloadLifecycleJob.status == DownloadJobStatus.RUNNING,
                DownloadLifecycleJob.lease_owner == worker_id,
            )
            .values(phase=phase)
        )
        return _changed(result)

    async def begin_delivery(self, job_id: int, worker_id: str, now: datetime) -> bool:
        result = await self._session.execute(
            update(DownloadLifecycleJob)
            .where(
                DownloadLifecycleJob.id == job_id,
                DownloadLifecycleJob.status == DownloadJobStatus.RUNNING,
                DownloadLifecycleJob.lease_owner == worker_id,
                DownloadLifecycleJob.cancel_requested_at.is_(None),
            )
            .values(status=DownloadJobStatus.DELIVERING, phase=None)
        )
        if _changed(result):
            await self._session.execute(
                update(DownloadDelivery)
                .where(
                    DownloadDelivery.job_id == job_id,
                    DownloadDelivery.status == DownloadDeliveryStatus.PENDING,
                )
                .values(
                    status=DownloadDeliveryStatus.SENDING,
                    attempt=DownloadDelivery.attempt + 1,
                )
            )
        return _changed(result)

    async def succeed(
        self,
        *,
        job_id: int,
        worker_id: str,
        message_id: int | None,
        file_id: str | None,
        now: datetime,
    ) -> bool:
        delivery = await self._session.execute(
            update(DownloadDelivery)
            .where(
                DownloadDelivery.job_id == job_id,
                DownloadDelivery.status.in_(
                    (DownloadDeliveryStatus.PENDING, DownloadDeliveryStatus.SENDING)
                ),
            )
            .values(
                status=DownloadDeliveryStatus.DELIVERED,
                telegram_message_id=message_id,
                telegram_file_id=file_id,
                delivered_at=now,
            )
        )
        if not _changed(delivery):
            return False
        result = await self._session.execute(
            update(DownloadLifecycleJob)
            .where(
                DownloadLifecycleJob.id == job_id,
                DownloadLifecycleJob.status == DownloadJobStatus.DELIVERING,
                DownloadLifecycleJob.lease_owner == worker_id,
            )
            .values(
                status=DownloadJobStatus.SUCCEEDED,
                finished_at=now,
                lease_owner=None,
                lease_expires_at=None,
                phase=None,
            )
        )
        return _changed(result)

    async def schedule_retry(
        self,
        *,
        job_id: int,
        worker_id: str,
        now: datetime,
        retry_at: datetime,
        error_code: str,
        error_message: str | None = None,
    ) -> DownloadJobStatus | None:
        job = await self.get_job(job_id)
        if (
            job is None
            or job.status not in (DownloadJobStatus.RUNNING, DownloadJobStatus.DELIVERING)
            or job.lease_owner != worker_id
        ):
            return None
        terminal = job.attempt >= job.max_attempts
        target = DownloadJobStatus.FAILED if terminal else DownloadJobStatus.RETRY_WAIT
        result = await self._session.execute(
            update(DownloadLifecycleJob)
            .where(
                DownloadLifecycleJob.id == job_id,
                DownloadLifecycleJob.lease_owner == worker_id,
                DownloadLifecycleJob.status.in_(
                    (DownloadJobStatus.RUNNING, DownloadJobStatus.DELIVERING)
                ),
            )
            .values(
                status=target,
                retry_at=None if terminal else retry_at,
                finished_at=now if terminal else None,
                error_code=error_code,
                error_message=error_message,
                lease_owner=None,
                lease_expires_at=None,
                phase=None,
            )
        )
        return target if _changed(result) else None

    async def fail(
        self,
        *,
        job_id: int,
        worker_id: str,
        now: datetime,
        error_code: str,
        error_message: str | None = None,
    ) -> bool:
        result = await self._session.execute(
            update(DownloadLifecycleJob)
            .where(
                DownloadLifecycleJob.id == job_id,
                DownloadLifecycleJob.status.in_(
                    (
                        DownloadJobStatus.RUNNING,
                        DownloadJobStatus.DELIVERING,
                        DownloadJobStatus.RETRY_WAIT,
                        DownloadJobStatus.QUEUED,
                    )
                ),
                or_(
                    DownloadLifecycleJob.lease_owner == worker_id,
                    DownloadLifecycleJob.lease_owner.is_(None),
                ),
            )
            .values(
                status=DownloadJobStatus.FAILED,
                finished_at=now,
                error_code=error_code,
                error_message=error_message,
                lease_owner=None,
                lease_expires_at=None,
                phase=None,
            )
        )
        return _changed(result)

    async def recover_expired(self, *, now: datetime, jitter_retry_at: datetime) -> int:
        result = await self._session.execute(
            update(DownloadLifecycleJob)
            .where(
                DownloadLifecycleJob.status.in_(
                    (DownloadJobStatus.RUNNING, DownloadJobStatus.DELIVERING)
                ),
                DownloadLifecycleJob.lease_expires_at.is_not(None),
                DownloadLifecycleJob.lease_expires_at <= now,
            )
            .values(
                status=case(
                    (
                        DownloadLifecycleJob.attempt >= DownloadLifecycleJob.max_attempts,
                        DownloadJobStatus.FAILED,
                    ),
                    else_=DownloadJobStatus.RETRY_WAIT,
                ),
                retry_at=case(
                    (
                        DownloadLifecycleJob.attempt >= DownloadLifecycleJob.max_attempts,
                        None,
                    ),
                    else_=jitter_retry_at,
                ),
                error_code="WORKER_LOST",
                finished_at=case(
                    (
                        DownloadLifecycleJob.attempt >= DownloadLifecycleJob.max_attempts,
                        now,
                    ),
                    else_=None,
                ),
                lease_owner=None,
                lease_expires_at=None,
                phase=None,
            )
        )
        return _rowcount(result)

    async def requeue_due(self, now: datetime) -> int:
        result = await self._session.execute(
            update(DownloadLifecycleJob)
            .where(
                DownloadLifecycleJob.status == DownloadJobStatus.RETRY_WAIT,
                DownloadLifecycleJob.retry_at <= now,
            )
            .values(status=DownloadJobStatus.QUEUED, queued_at=now, retry_at=None)
        )
        return _rowcount(result)

    async def cancel(self, job_id: int, now: datetime) -> DownloadJobStatus | None:
        result = await self._session.execute(
            update(DownloadLifecycleJob)
            .where(
                DownloadLifecycleJob.id == job_id,
                DownloadLifecycleJob.status.in_(
                    (
                        DownloadJobStatus.PENDING,
                        DownloadJobStatus.QUEUED,
                        DownloadJobStatus.RETRY_WAIT,
                    )
                ),
            )
            .values(
                status=DownloadJobStatus.CANCELLED,
                cancel_requested_at=now,
                finished_at=now,
                phase=None,
            )
        )
        if _changed(result):
            return DownloadJobStatus.CANCELLED
        result = await self._session.execute(
            update(DownloadLifecycleJob)
            .where(
                DownloadLifecycleJob.id == job_id,
                DownloadLifecycleJob.status.in_(
                    (DownloadJobStatus.RUNNING, DownloadJobStatus.DELIVERING)
                ),
            )
            .values(cancel_requested_at=now)
        )
        if _changed(result):
            return DownloadJobStatus.RUNNING
        job = await self.get_job(job_id)
        return job.status if job else None

    async def cancel_at_safe_point(self, job_id: int, worker_id: str, now: datetime) -> bool:
        result = await self._session.execute(
            update(DownloadLifecycleJob)
            .where(
                DownloadLifecycleJob.id == job_id,
                DownloadLifecycleJob.status == DownloadJobStatus.RUNNING,
                DownloadLifecycleJob.lease_owner == worker_id,
                DownloadLifecycleJob.cancel_requested_at.is_not(None),
            )
            .values(
                status=DownloadJobStatus.CANCELLED,
                finished_at=now,
                lease_owner=None,
                lease_expires_at=None,
                phase=None,
            )
        )
        return _changed(result)


def _changed(result: Any) -> bool:
    return _rowcount(result) > 0


def _rowcount(result: Any) -> int:
    return max(0, cast(CursorResult[Any], result).rowcount)
