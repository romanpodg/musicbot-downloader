"""Durable SingleFlight admission and persistent subscriber services."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from app.core.enums import QualityProfile, SubscriberStatus
from app.core.exceptions import InvalidRequestKeyError, SubscriberNotFoundError
from app.core.models import (
    JobSubscriberView,
    SingleFlightSnapshot,
    SingleFlightSubmission,
    SubscriberStatusCounts,
)
from app.services.queues import MAX_PAGE_SIZE, UploadQueueService, download_job_view
from app.storage import Database
from app.storage.models.base import utc_now
from app.storage.repositories.singleflight import SubscriberRecord

logger = logging.getLogger(__name__)

MAX_REQUEST_KEY_LENGTH = 128
DEFAULT_WAIT_POLL_SECONDS = 0.25


class SharedOperationCanceller(Protocol):
    async def cancel_download_operation(self, job_id: int) -> None: ...

    async def cancel_upload_operation(self, job_id: int) -> None: ...


class SubscriberNotifier:
    """Process-local wake-up optimization; SQLite remains authoritative."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._generation = 0

    @property
    def generation(self) -> int:
        return self._generation

    async def notify_all(self) -> None:
        async with self._condition:
            self._generation += 1
            self._condition.notify_all()

    async def wait_for_change(self, generation: int, wait_seconds: float) -> None:
        async with self._condition:
            if self._generation != generation:
                return
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(lambda: self._generation != generation),
                    timeout=wait_seconds,
                )
            except TimeoutError:
                pass


class SingleFlightService:
    """Normal admission path for shared Track + QualityProfile work.

    ``DownloadQueueService.submit`` remains the low-level raw queue primitive for
    maintenance and focused queue tests. User-originating work should enter here.
    """

    def __init__(
        self,
        database: Database,
        *,
        max_size: int,
        clock: Callable[[], datetime] = utc_now,
        download_wake_event: asyncio.Event | None = None,
        upload_wake_event: asyncio.Event | None = None,
        notifier: SubscriberNotifier | None = None,
        upload_queue: UploadQueueService | None = None,
        wait_poll_seconds: float = DEFAULT_WAIT_POLL_SECONDS,
    ) -> None:
        self._database = database
        self._max_size = max_size
        self._clock = clock
        self._download_wake_event = download_wake_event
        self._upload_wake_event = upload_wake_event
        self._notifier = notifier or SubscriberNotifier()
        self._upload_queue = upload_queue
        self._wait_poll_seconds = wait_poll_seconds
        self._canceller: SharedOperationCanceller | None = None

    @property
    def notifier(self) -> SubscriberNotifier:
        return self._notifier

    def attach_operation_canceller(self, canceller: SharedOperationCanceller) -> None:
        self._canceller = canceller

    async def submit(
        self,
        *,
        track_id: int,
        quality_profile: QualityProfile,
        request_key: str | None = None,
    ) -> SingleFlightSubmission:
        if not isinstance(quality_profile, QualityProfile):
            raise ValueError("invalid quality profile")
        _validate_request_key(request_key)
        async with self._database.transaction() as repositories:
            record = await repositories.singleflight.submit(
                track_id=track_id,
                quality_profile=quality_profile,
                request_key=request_key,
                max_active=self._max_size,
                now=self._clock(),
            )
            result = SingleFlightSubmission(
                subscriber=_subscriber_view(
                    SubscriberRecord(record.subscriber, record.download_job)
                ),
                download_job=download_job_view(record.download_job),
                created_new_job=record.created_new_job,
                joined_existing_flight=not record.created_new_job,
                returned_existing_subscriber=record.returned_existing_subscriber,
            )
        if record.created_new_job and self._download_wake_event is not None:
            self._download_wake_event.set()
        if record.reconciled_terminal_state:
            await self._notifier.notify_all()
        logger.info(
            "SingleFlight submission persisted",
            extra={
                "download_job_id": result.download_job.id,
                "subscriber_id": result.subscriber.id,
                "track_id": track_id,
                "quality_profile": quality_profile.value,
                "flight_joined": result.joined_existing_flight,
                "subscriber_status": result.subscriber.status.value,
            },
        )
        return result

    async def get_subscriber(self, subscriber_id: str) -> JobSubscriberView:
        async with self._database.transaction() as repositories:
            record = await repositories.singleflight.get_subscriber(subscriber_id)
            if record is None:
                raise SubscriberNotFoundError()
            return _subscriber_view(record)

    async def list_job_subscribers(
        self, download_job_id: int, *, offset: int = 0, limit: int = 50
    ) -> tuple[JobSubscriberView, ...]:
        _validate_page(offset, limit)
        async with self._database.transaction() as repositories:
            records = await repositories.singleflight.list_job_subscribers(
                download_job_id, offset=offset, limit=limit
            )
            return tuple(_subscriber_view(record) for record in records)

    async def subscriber_counts(self, download_job_id: int | None = None) -> SubscriberStatusCounts:
        async with self._database.transaction() as repositories:
            values = await repositories.singleflight.subscriber_counts(download_job_id)
        return _subscriber_counts(values)

    async def snapshot(self) -> SingleFlightSnapshot:
        async with self._database.transaction() as repositories:
            active = await repositories.singleflight.active_flight_count()
            values = await repositories.singleflight.subscriber_counts()
        return SingleFlightSnapshot(active, _subscriber_counts(values))

    async def cancel_subscriber(self, subscriber_id: str) -> JobSubscriberView:
        async with self._database.transaction() as repositories:
            record = await repositories.singleflight.cancel_subscriber(subscriber_id, self._clock())
            if record is None:
                raise SubscriberNotFoundError()
            view = _subscriber_view(SubscriberRecord(record.subscriber, record.download_job))

        if record.release_artifact is not None and self._upload_queue is not None:
            self._upload_queue.release_owned(*record.release_artifact)
        if record.cancel_download_operation is not None and self._canceller is not None:
            await self._canceller.cancel_download_operation(record.cancel_download_operation)
        if record.cancel_upload_operation is not None and self._canceller is not None:
            await self._canceller.cancel_upload_operation(record.cancel_upload_operation)
        if self._download_wake_event is not None:
            self._download_wake_event.set()
        if self._upload_wake_event is not None:
            self._upload_wake_event.set()
        await self._notifier.notify_all()
        return view

    async def reconcile(self) -> int:
        async with self._database.transaction() as repositories:
            closed = await repositories.singleflight.reconcile_all(self._clock())
        if closed:
            await self._notifier.notify_all()
        return closed

    async def wait(  # noqa: ASYNC109 - timeout is the public API term.
        self,
        subscriber_id: str,
        *,
        timeout: float | None = None,  # noqa: ASYNC109 - public API term
    ) -> JobSubscriberView:
        async def wait_until_terminal() -> JobSubscriberView:
            while True:
                generation = self._notifier.generation
                subscriber = await self.get_subscriber(subscriber_id)
                if subscriber.status is not SubscriberStatus.WAITING:
                    return subscriber
                await self._notifier.wait_for_change(generation, self._wait_poll_seconds)

        if timeout is None:
            return await wait_until_terminal()
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        async with asyncio.timeout(timeout):
            return await wait_until_terminal()


def _subscriber_view(record: SubscriberRecord) -> JobSubscriberView:
    subscriber = record.subscriber
    job = record.download_job
    return JobSubscriberView(
        subscriber.id,
        subscriber.download_job_id,
        job.track_id,
        job.quality_profile,
        subscriber.status,
        subscriber.request_key,
        subscriber.created_at,
        subscriber.updated_at,
        subscriber.completed_at,
        subscriber.last_error_code,
    )


def _subscriber_counts(values: dict[SubscriberStatus, int]) -> SubscriberStatusCounts:
    return SubscriberStatusCounts(
        waiting=values.get(SubscriberStatus.WAITING, 0),
        ready=values.get(SubscriberStatus.READY, 0),
        failed=values.get(SubscriberStatus.FAILED, 0),
        cancelled=values.get(SubscriberStatus.CANCELLED, 0),
    )


def _validate_request_key(request_key: str | None) -> None:
    if request_key is not None and (not request_key or len(request_key) > MAX_REQUEST_KEY_LENGTH):
        raise InvalidRequestKeyError()


def _validate_page(offset: int, limit: int) -> None:
    if offset < 0 or limit < 1 or limit > MAX_PAGE_SIZE:
        raise ValueError("invalid pagination")
