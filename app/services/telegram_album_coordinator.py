"""Restart-safe expansion of selected album positions into ordinary deliveries."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from app.core.enums import AlbumRequestStatus
from app.core.exceptions import (
    DatabaseConcurrencyError,
    MetadataUnavailable,
    ProviderAuthenticationError,
    ProviderUnavailable,
)
from app.i18n import LocalizationService
from app.services.track_resolution import ResolveTrackService
from app.storage import Database
from app.storage.models import TelegramAlbumItem
from app.storage.models.base import utc_now
from app.telegram import TelegramGateway, TelegramGatewayError

logger = logging.getLogger(__name__)

ALBUM_ITEM_MAX_ATTEMPTS = 3
DEFAULT_ALBUM_LEASE_SECONDS = 120.0
DEFAULT_ALBUM_POLL_SECONDS = 0.5


class TelegramAlbumCoordinator:
    def __init__(
        self,
        database: Database,
        resolver: ResolveTrackService,
        gateway: TelegramGateway,
        i18n: LocalizationService,
        *,
        album_wake_event: asyncio.Event,
        delivery_wake_event: asyncio.Event,
        lease_seconds: float = DEFAULT_ALBUM_LEASE_SECONDS,
        max_attempts: int = ALBUM_ITEM_MAX_ATTEMPTS,
    ) -> None:
        self._database = database
        self._resolver = resolver
        self._gateway = gateway
        self._i18n = i18n
        self._album_wake = album_wake_event
        self._delivery_wake = delivery_wake_event
        self._lease = timedelta(seconds=lease_seconds)
        self._max_attempts = max_attempts

    async def claim(self, worker_id: str) -> TelegramAlbumItem | None:
        now = utc_now()
        async with self._database.transaction() as repositories:
            await repositories.telegram_album.recover_expired(
                now=now, max_attempts=self._max_attempts
            )
            return await repositories.telegram_album.claim_item(
                worker_id=worker_id,
                now=now,
                lease_expires_at=now + self._lease,
            )

    async def process(self, item: TelegramAlbumItem, worker_id: str) -> None:
        try:
            async with self._database.transaction() as repositories:
                request = await repositories.telegram_album.get(item.album_request_id)
                if request is None:
                    await repositories.telegram_album.retry_or_fail(
                        item_id=item.id,
                        worker_id=worker_id,
                        retryable=False,
                        max_attempts=self._max_attempts,
                        available_at=utc_now(),
                        error_code="ALBUM_REQUEST_MISSING",
                    )
                    return
                source = await repositories.track_sources.get_source(
                    request.provider, item.provider_track_id
                )
                track_id = source.track_id if source is not None else None

            if track_id is None:
                resolved = await self._resolver.resolve_provider_track(
                    request.provider, item.provider_track_id, discover=True
                )
                track_id = resolved.track.id

            async with self._database.transaction() as repositories:
                current_request = await repositories.telegram_album.get(item.album_request_id)
                current_item = await repositories.telegram_album.list_items(
                    item.album_request_id, offset=item.position - 1, limit=1
                )
                if (
                    current_request is None
                    or not current_item
                    or current_item[0].id != item.id
                    or current_request.status
                    not in (AlbumRequestStatus.QUEUED, AlbumRequestStatus.PROCESSING)
                    or current_request.quality_profile is None
                ):
                    await repositories.telegram_album.retry_or_fail(
                        item_id=item.id,
                        worker_id=worker_id,
                        retryable=False,
                        max_attempts=self._max_attempts,
                        available_at=utc_now(),
                        error_code="ALBUM_REQUEST_STALE",
                    )
                    return
                await repositories.telegram_delivery.create_album_child(
                    telegram_bot_id=current_request.telegram_bot_id,
                    user_id=current_request.user_id,
                    telegram_chat_id=current_request.telegram_chat_id,
                    album_item_id=item.id,
                    track_id=track_id,
                    quality_profile=current_request.quality_profile,
                    now=utc_now(),
                )
                attached = await repositories.telegram_album.attach(
                    item_id=item.id, worker_id=worker_id, track_id=track_id
                )
            if attached:
                self._delivery_wake.set()
        except asyncio.CancelledError:
            await self._retry(item, worker_id, "ALBUM_ITEM_CANCELLED", retryable=True)
            raise
        except (ProviderUnavailable, ProviderAuthenticationError, DatabaseConcurrencyError):
            await self._retry(item, worker_id, "ALBUM_ITEM_PROVIDER_UNAVAILABLE", retryable=True)
        except MetadataUnavailable:
            await self._retry(item, worker_id, "ALBUM_ITEM_METADATA_UNAVAILABLE", retryable=False)
        except Exception:
            logger.exception(
                "Album item expansion failed",
                extra={"album_item_id": item.id, "album_request_id": item.album_request_id},
            )
            await self._retry(item, worker_id, "ALBUM_ITEM_FAILED", retryable=True)
        finally:
            self._album_wake.set()

    async def reconcile(self) -> int:
        changed = 0
        async with self._database.transaction() as repositories:
            requests = await repositories.telegram_album.list_reconcilable()
            for request in requests:
                aggregate = await repositories.telegram_album.aggregate(request.id)
                unresolved = aggregate.selected - aggregate.item_failed - aggregate.attached
                if unresolved > 0 or aggregate.delivery_active > 0:
                    continue
                terminal_deliveries = (
                    aggregate.delivered + aggregate.delivery_failed + aggregate.delivery_cancelled
                )
                if terminal_deliveries != aggregate.attached:
                    continue
                if aggregate.delivered == aggregate.selected:
                    status = AlbumRequestStatus.COMPLETED
                elif aggregate.delivered == 0:
                    status = AlbumRequestStatus.FAILED
                else:
                    status = AlbumRequestStatus.PARTIALLY_FAILED
                if await repositories.telegram_album.mark_terminal(
                    request_id=request.id, status=status, now=utc_now()
                ):
                    changed += 1
        await self._notify_terminal()
        return changed

    async def _notify_terminal(self) -> None:
        async with self._database.transaction() as repositories:
            requests = await repositories.telegram_album.list_unnotified_terminal()
        for request in requests:
            async with self._database.transaction() as repositories:
                aggregate = await repositories.telegram_album.aggregate(request.id)
                user = await repositories.users.get(request.user_id)
            locale = (
                self._i18n.resolve_locale(user.preferred_locale, user.telegram_language_code)
                if user is not None
                else self._i18n.default_locale
            )
            key = {
                AlbumRequestStatus.COMPLETED: "bot.album_completed",
                AlbumRequestStatus.PARTIALLY_FAILED: "bot.album_partially_failed",
                AlbumRequestStatus.FAILED: "bot.album_failed",
            }[request.status]
            failed = (
                aggregate.item_failed + aggregate.delivery_failed + aggregate.delivery_cancelled
            )
            try:
                receipt = await self._gateway.send_text(
                    request.telegram_chat_id,
                    self._i18n.translate(
                        key,
                        locale,
                        delivered=aggregate.delivered,
                        failed=failed,
                        total=aggregate.selected,
                    ),
                )
            except TelegramGatewayError:
                continue
            async with self._database.transaction() as repositories:
                await repositories.telegram_album.mark_notified(
                    request_id=request.id, message_id=receipt.message_id, now=utc_now()
                )

    async def _retry(
        self,
        item: TelegramAlbumItem,
        worker_id: str,
        error_code: str,
        *,
        retryable: bool,
    ) -> None:
        delay = min(2 ** max(item.attempt_count - 1, 0), 30)
        async with self._database.transaction() as repositories:
            await repositories.telegram_album.retry_or_fail(
                item_id=item.id,
                worker_id=worker_id,
                retryable=retryable,
                max_attempts=self._max_attempts,
                available_at=utc_now() + timedelta(seconds=delay),
                error_code=error_code,
            )


class TelegramAlbumCoordinatorManager:
    def __init__(
        self,
        coordinator: TelegramAlbumCoordinator,
        *,
        wake_event: asyncio.Event,
        workers: int = 1,
        poll_seconds: float = DEFAULT_ALBUM_POLL_SECONDS,
    ) -> None:
        if workers < 1:
            raise ValueError("album workers must be positive")
        self._coordinator = coordinator
        self._wake = wake_event
        self._workers = workers
        self._poll_seconds = poll_seconds
        self._running = False
        self._tasks: list[asyncio.Task[None]] = []
        self._reconciler: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._run(index + 1), name=f"telegram-album-{index + 1}")
            for index in range(self._workers)
        ]
        self._reconciler = asyncio.create_task(self._reconcile(), name="telegram-album-reconcile")
        self._wake.set()

    async def stop(self) -> None:
        self._running = False
        self._wake.set()
        tasks = list(self._tasks)
        if self._reconciler is not None:
            tasks.append(self._reconciler)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._reconciler = None

    async def _run(self, number: int) -> None:
        worker_id = f"telegram-album-{number}"
        while self._running:
            try:
                item = await self._coordinator.claim(worker_id)
                if item is None:
                    await self._wait()
                    continue
                await self._coordinator.process(item, worker_id)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Album coordinator loop failed")
                await asyncio.sleep(min(self._poll_seconds, 1.0))

    async def _reconcile(self) -> None:
        while self._running:
            try:
                await self._coordinator.reconcile()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Album completion reconciliation failed")
            await asyncio.sleep(self._poll_seconds)

    async def _wait(self) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=self._poll_seconds)
        except TimeoutError:
            pass
        finally:
            self._wake.clear()
