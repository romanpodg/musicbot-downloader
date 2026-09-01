"""Cache-first delivery preparation boundary for future Telegram handlers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime

from app.core.enums import DeliveryPreparationStatus, QualityProfile, SubscriberStatus
from app.core.exceptions import (
    DeliveryInvariantError,
    InvalidRequestKeyError,
    SubscriberNotFoundError,
    SubscriberNotReadyError,
)
from app.core.models import (
    CachedTelegramFile,
    DeliveryPreparationResult,
    JobSubscriberView,
    SingleFlightSubmission,
)
from app.services.queues import SubscriberLifecycleNotifier, download_job_view
from app.services.telegram_cache import cache_view
from app.storage import Database
from app.storage.models.base import utc_now
from app.storage.repositories.singleflight import AdmissionRecord, SubscriberRecord

MAX_REQUEST_KEY_LENGTH = 128


class DeliveryPreparationService:
    def __init__(
        self,
        database: Database,
        *,
        telegram_bot_id: int,
        max_size: int,
        clock: Callable[[], datetime] = utc_now,
        download_wake_event: asyncio.Event | None = None,
        notifier: SubscriberLifecycleNotifier | None = None,
    ) -> None:
        if telegram_bot_id <= 0:
            raise ValueError("invalid Telegram bot ID")
        self._database = database
        self._telegram_bot_id = telegram_bot_id
        self._max_size = max_size
        self._clock = clock
        self._download_wake_event = download_wake_event
        self._notifier = notifier

    async def prepare(
        self,
        *,
        track_id: int,
        quality_profile: QualityProfile,
        request_key: str | None = None,
        artifact_fingerprint: str | None = None,
    ) -> DeliveryPreparationResult:
        if not isinstance(quality_profile, QualityProfile):
            raise ValueError("invalid quality profile")
        if request_key is not None and (
            not request_key or len(request_key) > MAX_REQUEST_KEY_LENGTH
        ):
            raise InvalidRequestKeyError()

        admission: AdmissionRecord | None = None
        async with self._database.transaction() as repositories:
            await repositories.telegram_cache.lock_for_admission()
            cached = await repositories.telegram_cache.get_active(
                telegram_bot_id=self._telegram_bot_id,
                track_id=track_id,
                quality_profile=quality_profile,
                artifact_fingerprint=artifact_fingerprint,
            )
            if cached is not None:
                return DeliveryPreparationResult(
                    DeliveryPreparationStatus.CACHE_HIT,
                    track_id,
                    quality_profile,
                    cached_file=cache_view(cached),
                )
            admission = await repositories.singleflight.submit(
                track_id=track_id,
                quality_profile=quality_profile,
                request_key=request_key,
                artifact_fingerprint=artifact_fingerprint,
                max_active=self._max_size,
                now=self._clock(),
                acquire_lock=False,
            )
            submission = _submission_view(admission)

        if admission.created_new_job and self._download_wake_event is not None:
            self._download_wake_event.set()
        if admission.reconciled_terminal_state and self._notifier is not None:
            await self._notifier.notify_all()
        return DeliveryPreparationResult(
            DeliveryPreparationStatus.PENDING,
            track_id,
            quality_profile,
            subscriber=submission.subscriber,
            download_job_id=submission.download_job.id,
        )

    async def get_ready_file(self, subscriber_id: str) -> CachedTelegramFile:
        async with self._database.transaction() as repositories:
            record = await repositories.singleflight.get_subscriber(subscriber_id)
            if record is None:
                raise SubscriberNotFoundError()
            if record.subscriber.status is not SubscriberStatus.READY:
                raise SubscriberNotReadyError()
            cached = await repositories.telegram_cache.get_active(
                telegram_bot_id=self._telegram_bot_id,
                track_id=record.download_job.track_id,
                quality_profile=record.download_job.quality_profile,
                artifact_fingerprint=record.download_job.artifact_fingerprint,
            )
            if cached is None:
                raise DeliveryInvariantError()
            return cache_view(cached)


def _submission_view(record: AdmissionRecord) -> SingleFlightSubmission:
    return SingleFlightSubmission(
        subscriber=_subscriber_view(SubscriberRecord(record.subscriber, record.download_job)),
        download_job=download_job_view(record.download_job),
        created_new_job=record.created_new_job,
        joined_existing_flight=not record.created_new_job,
        returned_existing_subscriber=record.returned_existing_subscriber,
    )


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
