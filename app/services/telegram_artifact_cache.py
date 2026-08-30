"""Stage 24 cache service; it never authorizes or creates downloads."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from app.core.telegram_artifact_cache import TelegramCacheKey
from app.storage import Database
from app.storage.models import TelegramArtifactCacheEntry
from app.storage.models.base import utc_now
from app.storage.models.download_lifecycle import DownloadRequestRecord


class TelegramArtifactCacheService:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._database = database
        self._clock = clock

    async def lookup(self, request: DownloadRequestRecord) -> TelegramArtifactCacheEntry | None:
        key = TelegramCacheKey.from_request(request)
        if key is None:
            return None
        async with self._database.transaction() as repositories:
            return await repositories.telegram_artifact_cache.get_active(key.fingerprint)

    async def record_successful_delivery(
        self, request: DownloadRequestRecord, *, delivery_id: int, file_id: str
    ) -> TelegramArtifactCacheEntry | None:
        key = TelegramCacheKey.from_request(request)
        if key is None or not file_id:
            return None
        async with self._database.transaction() as repositories:
            return await repositories.telegram_artifact_cache.upsert(
                key=key, file_id=file_id, source_delivery_id=delivery_id, now=self._clock()
            )

    async def mark_used(self, entry_id: int) -> bool:
        async with self._database.transaction() as repositories:
            return await repositories.telegram_artifact_cache.mark_used(entry_id, self._clock())

    async def invalidate(self, entry_id: int, reason: str) -> bool:
        async with self._database.transaction() as repositories:
            return await repositories.telegram_artifact_cache.invalidate(
                entry_id, reason, self._clock()
            )

    async def prune(
        self, *, max_entries: int, invalid_retention: timedelta = timedelta(days=7)
    ) -> int:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        now = self._clock()
        async with self._database.transaction() as repositories:
            return await repositories.telegram_artifact_cache.prune(
                max_entries=max_entries, now=now, invalid_before=now - invalid_retention
            )
