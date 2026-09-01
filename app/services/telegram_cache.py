"""Application service for durable bot-scoped Telegram file references."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime

from app.core.enums import QualityProfile, TelegramCacheStatus
from app.core.exceptions import (
    DatabaseConcurrencyError,
    TelegramCacheEntryNotFoundError,
)
from app.core.models import (
    CachedTelegramFile,
    DownloadArtifactMetadata,
    TelegramCacheStats,
    TelegramUploadReceipt,
)
from app.storage import Database
from app.storage.models import TelegramFileCache
from app.storage.models.base import utc_now

MAX_CACHE_PAGE_SIZE = 100
MAX_INVALID_REASON_LENGTH = 64


class TelegramFileCacheService:
    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utc_now,
        persistence_attempts: int = 3,
    ) -> None:
        self._database = database
        self._clock = clock
        self._persistence_attempts = persistence_attempts

    async def get_active(
        self,
        *,
        telegram_bot_id: int,
        track_id: int,
        quality_profile: QualityProfile,
        artifact_fingerprint: str | None = None,
    ) -> CachedTelegramFile | None:
        async with self._database.transaction() as repositories:
            cached = await repositories.telegram_cache.get_active(
                telegram_bot_id=telegram_bot_id,
                track_id=track_id,
                quality_profile=quality_profile,
                artifact_fingerprint=artifact_fingerprint,
            )
            return _cache_view(cached) if cached is not None else None

    async def get(self, cache_id: int) -> CachedTelegramFile:
        async with self._database.transaction() as repositories:
            cached = await repositories.telegram_cache.get(cache_id)
            if cached is None:
                raise TelegramCacheEntryNotFoundError()
            return _cache_view(cached)

    async def list_entries(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        status: TelegramCacheStatus | None = None,
        track_id: int | None = None,
        telegram_bot_id: int | None = None,
    ) -> tuple[CachedTelegramFile, ...]:
        if offset < 0 or limit < 1 or limit > MAX_CACHE_PAGE_SIZE:
            raise ValueError("invalid pagination")
        async with self._database.transaction() as repositories:
            rows = await repositories.telegram_cache.list(
                offset=offset,
                limit=limit,
                status=status,
                track_id=track_id,
                telegram_bot_id=telegram_bot_id,
            )
            return tuple(_cache_view(row) for row in rows)

    async def stats(self, *, telegram_bot_id: int | None = None) -> TelegramCacheStats:
        async with self._database.transaction() as repositories:
            active, invalid, media_bytes = await repositories.telegram_cache.stats(telegram_bot_id)
        return TelegramCacheStats(active, invalid, media_bytes)

    async def upsert_success(
        self,
        *,
        track_id: int,
        quality_profile: QualityProfile,
        artifact_fingerprint: str | None = None,
        receipt: TelegramUploadReceipt,
        artifact: DownloadArtifactMetadata,
    ) -> CachedTelegramFile:
        _validate_receipt(receipt, artifact)
        last_error: DatabaseConcurrencyError | None = None
        for attempt in range(self._persistence_attempts):
            try:
                async with self._database.transaction() as repositories:
                    cached = await repositories.telegram_cache.upsert_success(
                        track_id=track_id,
                        quality_profile=quality_profile,
                        artifact_fingerprint=artifact_fingerprint,
                        receipt=receipt,
                        artifact=artifact,
                        now=self._clock(),
                    )
                    return _cache_view(cached)
            except DatabaseConcurrencyError as exc:
                last_error = exc
                await asyncio.sleep(0.02 * (attempt + 1))
        if last_error is not None:
            raise last_error
        raise RuntimeError("cache persistence attempts must be positive")

    async def invalidate(
        self, cache_id: int, *, reason_code: str | None = None
    ) -> CachedTelegramFile:
        if reason_code is not None and (
            not reason_code or len(reason_code) > MAX_INVALID_REASON_LENGTH
        ):
            raise ValueError("invalid reason code")
        async with self._database.transaction() as repositories:
            cached = await repositories.telegram_cache.invalidate(
                cache_id, reason_code=reason_code, now=self._clock()
            )
            if cached is None:
                raise TelegramCacheEntryNotFoundError()
            return _cache_view(cached)

    async def mark_used(self, cache_id: int) -> bool:
        async with self._database.transaction() as repositories:
            return await repositories.telegram_cache.mark_used(cache_id, now=self._clock())


def _validate_receipt(receipt: TelegramUploadReceipt, artifact: DownloadArtifactMetadata) -> None:
    if (
        receipt.telegram_bot_id <= 0
        or receipt.message_id <= 0
        or not receipt.file_id
        or not receipt.file_unique_id
        or artifact.file_size_bytes <= 0
    ):
        raise ValueError("invalid Telegram upload receipt")


def cache_view(row: TelegramFileCache) -> CachedTelegramFile:
    return _cache_view(row)


def _cache_view(row: TelegramFileCache) -> CachedTelegramFile:
    return CachedTelegramFile(
        cache_id=row.id,
        telegram_bot_id=row.telegram_bot_id,
        track_id=row.track_id,
        quality_profile=row.quality_profile,
        artifact_fingerprint=row.artifact_fingerprint,
        file_id=row.telegram_file_id,
        file_unique_id=row.telegram_file_unique_id,
        media_kind=row.telegram_media_kind,
        cache_chat_id=row.cache_chat_id,
        cache_message_id=row.cache_message_id,
        file_size_bytes=row.file_size_bytes,
        source_track_source_id=row.source_track_source_id,
        source_provider=row.source_provider,
        source_provider_track_id=row.source_provider_track_id,
        operation=row.operation,
        transcoded=row.transcoded,
        source_codec=row.source_codec,
        source_container=row.source_container,
        source_bitrate_kbps=row.source_bitrate_kbps,
        output_codec=row.output_codec,
        output_container=row.output_container,
        output_bitrate_kbps=row.output_bitrate_kbps,
        sample_rate_hz=row.sample_rate_hz,
        bit_depth=row.bit_depth,
        channels=row.channels,
        duration_ms=row.duration_ms,
        encoder=row.encoder,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_used_at=row.last_used_at,
        invalidated_at=row.invalidated_at,
        invalid_reason_code=row.invalid_reason_code,
    )
