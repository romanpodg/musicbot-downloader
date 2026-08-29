"""Persistent Stage 9 cached-file delivery fanout."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from app.core.delivery_targets import DeliveryTargetType
from app.core.enums import (
    QueueErrorCode,
    SubscriberStatus,
    TelegramCacheStatus,
    TelegramDeliveryStatus,
    TelegramMediaKind,
)
from app.i18n import LocalizationService
from app.services.delivery import DeliveryPreparationService
from app.services.download_lifecycle import DownloadLifecycleService
from app.services.telegram_cache import TelegramFileCacheService
from app.storage import Database
from app.storage.models import TelegramDeliveryRequest
from app.storage.models.base import utc_now
from app.telegram import TelegramCachedMediaSpec, TelegramGateway, TelegramGatewayError

logger = logging.getLogger(__name__)

DEFAULT_LEASE_SECONDS = 60.0
DEFAULT_POLL_SECONDS = 0.5


class TelegramDeliveryWorker:
    def __init__(
        self,
        database: Database,
        preparation: DeliveryPreparationService,
        cache: TelegramFileCacheService,
        gateway: TelegramGateway,
        i18n: LocalizationService,
        *,
        max_attempts: int,
        wake_event: asyncio.Event,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        lifecycle: DownloadLifecycleService | None = None,
    ) -> None:
        self._database = database
        self._preparation = preparation
        self._cache = cache
        self._gateway = gateway
        self._i18n = i18n
        self._max_attempts = max_attempts
        self._wake_event = wake_event
        self._lease = timedelta(seconds=lease_seconds)
        self._lifecycle = lifecycle

    async def claim(self, worker_id: str) -> TelegramDeliveryRequest | None:
        now = utc_now()
        async with self._database.transaction() as repositories:
            await repositories.telegram_delivery.recover_expired(
                now=now, max_attempts=self._max_attempts
            )
            return await repositories.telegram_delivery.claim(
                worker_id=worker_id,
                now=now,
                lease_expires_at=now + self._lease,
            )

    async def process(self, request: TelegramDeliveryRequest, worker_id: str) -> None:
        try:
            if request.quality_profile is None:
                await self._terminal(request, worker_id, "QUALITY_NOT_SELECTED")
                return
            if request.cache_id is None and request.subscriber_id is None:
                prepared = await self._preparation.prepare(
                    track_id=request.track_id,
                    quality_profile=request.quality_profile,
                    request_key=self._request_key(request),
                )
                async with self._database.transaction() as repositories:
                    await repositories.telegram_delivery.set_preparation(
                        request_id=request.id,
                        worker_id=worker_id,
                        cache_id=(prepared.cached_file.cache_id if prepared.cached_file else None),
                        subscriber_id=(prepared.subscriber.id if prepared.subscriber else None),
                        download_job_id=prepared.download_job_id,
                        now=utc_now(),
                    )
                self._wake_event.set()
                return

            if request.cache_id is None and request.subscriber_id is not None:
                if not await self._resolve_subscriber(request, worker_id):
                    return
                async with self._database.transaction() as repositories:
                    refreshed = await repositories.telegram_delivery.get(request.id)
                    if refreshed is None:
                        return
                    request = refreshed

            if request.cache_id is None:
                await self._terminal(request, worker_id, "READY_CACHE_MISSING")
                return
            cached = await self._cache.get(request.cache_id)
            if cached.status is not TelegramCacheStatus.ACTIVE:
                await self._repair(request, worker_id, cached.cache_id)
                return
            spec = TelegramCachedMediaSpec(request.delivery_target.chat_id, cached.file_id)
            if cached.media_kind is TelegramMediaKind.AUDIO:
                receipt = await self._gateway.send_cached_audio(spec)
            else:
                receipt = await self._gateway.send_cached_document(spec)
            async with self._database.transaction() as repositories:
                delivered = await repositories.telegram_delivery.delivered(
                    request_id=request.id,
                    worker_id=worker_id,
                    message_id=receipt.message_id,
                    now=utc_now(),
                )
            if delivered:
                await self._cache.mark_used(cached.cache_id)
        except asyncio.CancelledError:
            await self._retry(request, worker_id, "DELIVERY_CANCELLED", retryable=True)
            raise
        except TelegramGatewayError as exc:
            if exc.invalid_cached_file and request.cache_id is not None:
                await self._repair(request, worker_id, request.cache_id)
                return
            if (
                exc.code == QueueErrorCode.TELEGRAM_PERMISSION_DENIED.value
                and request.delivery_target.target_type is DeliveryTargetType.PRIVATE_USER
                and request.delivery_target.chat_id != request.telegram_chat_id
            ):
                await self._send_private_delivery_notice(request)
            await self._retry(
                request,
                worker_id,
                exc.code,
                retryable=exc.retryable,
                retry_after_seconds=exc.retry_after_seconds,
            )
        except Exception:
            logger.exception(
                "Telegram delivery worker failure",
                extra={"telegram_request_id": request.id, "track_id": request.track_id},
            )
            await self._retry(request, worker_id, "DELIVERY_FAILED", retryable=True)
        finally:
            if self._lifecycle is not None:
                try:
                    async with self._database.transaction() as repositories:
                        current = await repositories.telegram_delivery.get(request.id)
                    if current is not None:
                        await self._lifecycle.reconcile_telegram_delivery(
                            request.id,
                            current.status.value,
                            current.last_error_code,
                        )
                except Exception:
                    logger.info(
                        "Could not reconcile download lifecycle",
                        extra={"telegram_request_id": request.id},
                    )

    async def _send_private_delivery_notice(self, request: TelegramDeliveryRequest) -> None:
        """Best-effort origin-chat guidance for an unreachable USER target."""

        try:
            locale = await self._request_locale(request)
            await self._gateway.send_text(
                request.telegram_chat_id,
                self._i18n.translate("bot.private_delivery_unavailable", locale),
            )
        except Exception:
            # A notice must never change the terminal delivery outcome or crash
            # the worker when the origin chat is also unavailable.
            logger.info(
                "Could not send private delivery guidance",
                extra={"telegram_request_id": request.id},
            )

    async def _resolve_subscriber(self, request: TelegramDeliveryRequest, worker_id: str) -> bool:
        assert request.subscriber_id is not None
        async with self._database.transaction() as repositories:
            record = await repositories.singleflight.get_subscriber(request.subscriber_id)
        if record is None:
            await self._terminal(request, worker_id, "SUBSCRIBER_NOT_FOUND")
            return False
        if record.subscriber.status is SubscriberStatus.CANCELLED:
            await self._terminal(
                request, worker_id, "REQUEST_CANCELLED", TelegramDeliveryStatus.CANCELLED
            )
            return False
        if record.subscriber.status is SubscriberStatus.FAILED:
            code = record.subscriber.last_error_code or "DELIVERY_FAILED"
            await self._send_failure_notice(request, code)
            await self._terminal(request, worker_id, code)
            return False
        if record.subscriber.status is not SubscriberStatus.READY:
            await self._retry(request, worker_id, "SUBSCRIBER_WAITING", retryable=True)
            return False
        cached = await self._preparation.get_ready_file(request.subscriber_id)
        async with self._database.transaction() as repositories:
            await repositories.telegram_delivery.attach_ready_cache(
                request_id=request.id, worker_id=worker_id, cache_id=cached.cache_id
            )
        request.cache_id = cached.cache_id
        return True

    async def _send_failure_notice(self, request: TelegramDeliveryRequest, code: str) -> None:
        locale = await self._request_locale(request)
        key = {
            "NO_AVAILABLE_PROVIDER": "bot.no_available_provider",
            "QUALITY_UNAVAILABLE": "bot.quality_unavailable",
            "AUTH_REQUIRED": "bot.no_available_provider",
        }.get(code, "bot.delivery_failed")
        try:
            await self._gateway.send_text(
                request.delivery_target.chat_id, self._i18n.translate(key, locale)
            )
        except TelegramGatewayError:
            logger.info(
                "Could not send terminal delivery notice",
                extra={"telegram_request_id": request.id},
            )

    async def _request_locale(self, request: TelegramDeliveryRequest) -> str:
        async with self._database.transaction() as repositories:
            user = await repositories.users.get(request.user_id)
        if user is None:
            return self._i18n.default_locale
        return self._i18n.resolve_locale(user.preferred_locale, user.telegram_language_code)

    async def _repair(
        self, request: TelegramDeliveryRequest, worker_id: str, cache_id: int
    ) -> None:
        if request.repair_count >= 1:
            await self._terminal(request, worker_id, "INVALID_CACHED_FILE")
            return
        await self._cache.invalidate(cache_id, reason_code="INVALID_CACHED_FILE")
        async with self._database.transaction() as repositories:
            scheduled = await repositories.telegram_delivery.schedule_repair(
                request_id=request.id, worker_id=worker_id, now=utc_now()
            )
        if scheduled:
            self._wake_event.set()

    async def _retry(
        self,
        request: TelegramDeliveryRequest,
        worker_id: str,
        code: str,
        *,
        retryable: bool,
        retry_after_seconds: float | None = None,
    ) -> None:
        backoff = min(2 ** max(request.attempt_count - 1, 0), 300)
        delay = max(backoff, retry_after_seconds or 0)
        async with self._database.transaction() as repositories:
            status = await repositories.telegram_delivery.retry_or_fail(
                request_id=request.id,
                worker_id=worker_id,
                retryable=retryable,
                max_attempts=self._max_attempts,
                available_at=utc_now() + timedelta(seconds=delay),
                error_code=code,
            )
        if status is TelegramDeliveryStatus.QUEUED:
            self._wake_event.set()

    async def _terminal(
        self,
        request: TelegramDeliveryRequest,
        worker_id: str,
        code: str,
        status: TelegramDeliveryStatus = TelegramDeliveryStatus.FAILED,
    ) -> None:
        async with self._database.transaction() as repositories:
            await repositories.telegram_delivery.fail_terminal(
                request_id=request.id, worker_id=worker_id, status=status, error_code=code
            )

    @staticmethod
    def _request_key(request: TelegramDeliveryRequest) -> str:
        if request.album_item_id is not None:
            return f"alb:{request.album_item_id}"
        if request.source_message_id is None:
            raise ValueError("Telegram delivery request has no durable origin")
        return (
            f"tg:{request.telegram_bot_id}:{request.telegram_chat_id}:{request.source_message_id}"
        )


class TelegramDeliveryFanoutManager:
    def __init__(
        self,
        worker: TelegramDeliveryWorker,
        *,
        workers: int,
        wake_event: asyncio.Event,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
    ) -> None:
        if workers < 1:
            raise ValueError("delivery workers must be positive")
        self._worker = worker
        self._workers = workers
        self._wake_event = wake_event
        self._poll_seconds = poll_seconds
        self._running = False
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._run(index + 1), name=f"telegram-delivery-{index + 1}")
            for index in range(self._workers)
        ]
        self._wake_event.set()

    async def stop(self) -> None:
        self._running = False
        self._wake_event.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _run(self, number: int) -> None:
        worker_id = f"telegram-delivery-{number}"
        while self._running:
            try:
                request = await self._worker.claim(worker_id)
                if request is None:
                    await self._wait()
                    continue
                await self._worker.process(request, worker_id)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Telegram delivery fanout loop failed")
                await asyncio.sleep(min(self._poll_seconds, 1.0))

    async def _wait(self) -> None:
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=self._poll_seconds)
        except TimeoutError:
            pass
        finally:
            self._wake_event.clear()
