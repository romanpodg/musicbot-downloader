"""Authorized, read-only operational overview for the Telegram admin panel."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.core.enums import TelegramDeliveryStatus
from app.core.exceptions import MusicBotError
from app.core.models import QueueRuntimeSnapshot, TelegramCacheStats
from app.services.authorization import (
    AdminAccessContext,
    AdminPermission,
    TelegramAuthorizationService,
)
from app.storage import Database
from app.storage.models.base import utc_now

logger = logging.getLogger(__name__)


class QueueSnapshotReader(Protocol):
    async def snapshot(self) -> QueueRuntimeSnapshot: ...


class TelegramCacheStatsReader(Protocol):
    async def stats(self, *, telegram_bot_id: int | None = None) -> TelegramCacheStats: ...


class AdminOverviewError(MusicBotError):
    """A local overview source failed; presentation should use a generic message."""


@dataclass(frozen=True, slots=True)
class DeliveryOverview:
    waiting_or_queued: int
    sending: int
    failed: int


@dataclass(frozen=True, slots=True)
class AlbumOverview:
    active: int


@dataclass(frozen=True, slots=True)
class AdminOverview:
    generated_at: datetime
    queues: QueueRuntimeSnapshot
    telegram_cache: TelegramCacheStats
    deliveries: DeliveryOverview
    albums: AlbumOverview


@dataclass(frozen=True, slots=True)
class AuthorizedAdminOverview:
    access: AdminAccessContext
    overview: AdminOverview


class AdminOverviewService:
    """Freshly authorize, then compose bounded local statistics only."""

    def __init__(
        self,
        database: Database,
        authorization: TelegramAuthorizationService,
        queues: QueueSnapshotReader,
        telegram_cache: TelegramCacheStatsReader,
        *,
        telegram_bot_id: int,
    ) -> None:
        self._database = database
        self._authorization = authorization
        self._queues = queues
        self._telegram_cache = telegram_cache
        self._telegram_bot_id = telegram_bot_id

    async def authorize_view(self, user_id: int) -> AdminAccessContext:
        return await self._authorization.require_permission(
            user_id, AdminPermission.ADMIN_PANEL_VIEW
        )

    async def get_overview(self, user_id: int) -> AuthorizedAdminOverview:
        access = await self.authorize_view(user_id)
        try:
            queue_snapshot = await self._queues.snapshot()
            cache_stats = await self._telegram_cache.stats(telegram_bot_id=self._telegram_bot_id)
            async with self._database.transaction() as repositories:
                delivery_counts = await repositories.telegram_delivery.status_counts()
                active_albums = await repositories.telegram_album.count_active()
        except Exception as exc:
            logger.error(
                "Admin overview collection failed",
                extra={"action": "admin_overview", "code": "local_statistics_failed"},
            )
            raise AdminOverviewError() from exc

        waiting_or_queued = sum(
            delivery_counts.get(status, 0)
            for status in (TelegramDeliveryStatus.QUEUED, TelegramDeliveryStatus.WAITING)
        )
        return AuthorizedAdminOverview(
            access,
            AdminOverview(
                generated_at=utc_now(),
                queues=queue_snapshot,
                telegram_cache=cache_stats,
                deliveries=DeliveryOverview(
                    waiting_or_queued=waiting_or_queued,
                    sending=delivery_counts.get(TelegramDeliveryStatus.SENDING, 0),
                    failed=delivery_counts.get(TelegramDeliveryStatus.FAILED, 0),
                ),
                albums=AlbumOverview(active=active_albums),
            ),
        )
