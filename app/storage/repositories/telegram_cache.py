"""SQLite operations for the bot-scoped Telegram completed-result cache."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, func, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import QualityProfile, TelegramCacheStatus
from app.core.models import DownloadArtifactMetadata, TelegramUploadReceipt
from app.storage.models import TelegramFileCache, TrackSource


class TelegramFileCacheRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_for_admission(self) -> None:
        """Reserve the SQLite writer lock before the cache/flight admission decision."""

        await self._session.execute(text("BEGIN IMMEDIATE"))

    async def get_active(
        self,
        *,
        telegram_bot_id: int,
        track_id: int,
        quality_profile: QualityProfile,
    ) -> TelegramFileCache | None:
        return (
            await self._session.scalars(
                select(TelegramFileCache).where(
                    TelegramFileCache.telegram_bot_id == telegram_bot_id,
                    TelegramFileCache.track_id == track_id,
                    TelegramFileCache.quality_profile == quality_profile,
                    TelegramFileCache.status == TelegramCacheStatus.ACTIVE,
                )
            )
        ).one_or_none()

    async def get(self, cache_id: int) -> TelegramFileCache | None:
        return await self._session.get(TelegramFileCache, cache_id)

    async def list(
        self,
        *,
        offset: int,
        limit: int,
        status: TelegramCacheStatus | None,
        track_id: int | None,
        telegram_bot_id: int | None,
    ) -> list[TelegramFileCache]:
        statement = select(TelegramFileCache)
        if status is not None:
            statement = statement.where(TelegramFileCache.status == status)
        if track_id is not None:
            statement = statement.where(TelegramFileCache.track_id == track_id)
        if telegram_bot_id is not None:
            statement = statement.where(TelegramFileCache.telegram_bot_id == telegram_bot_id)
        rows = await self._session.scalars(
            statement.order_by(TelegramFileCache.id.desc()).offset(offset).limit(limit)
        )
        return list(rows)

    async def stats(self, telegram_bot_id: int | None) -> tuple[int, int, int]:
        statement = select(
            func.sum(case((TelegramFileCache.status == TelegramCacheStatus.ACTIVE, 1), else_=0)),
            func.sum(case((TelegramFileCache.status == TelegramCacheStatus.INVALID, 1), else_=0)),
            func.sum(
                case(
                    (
                        TelegramFileCache.status == TelegramCacheStatus.ACTIVE,
                        TelegramFileCache.file_size_bytes,
                    ),
                    else_=0,
                )
            ),
        )
        if telegram_bot_id is not None:
            statement = statement.where(TelegramFileCache.telegram_bot_id == telegram_bot_id)
        active, invalid, media_bytes = (await self._session.execute(statement)).one()
        return int(active or 0), int(invalid or 0), int(media_bytes or 0)

    async def upsert_success(
        self,
        *,
        track_id: int,
        quality_profile: QualityProfile,
        receipt: TelegramUploadReceipt,
        artifact: DownloadArtifactMetadata,
        now: datetime,
    ) -> TelegramFileCache:
        source_id = artifact.track_source_id
        if source_id is not None and await self._session.get(TrackSource, source_id) is None:
            source_id = None
        values = {
            "telegram_bot_id": receipt.telegram_bot_id,
            "track_id": track_id,
            "quality_profile": quality_profile,
            "telegram_file_id": receipt.file_id,
            "telegram_file_unique_id": receipt.file_unique_id,
            "telegram_media_kind": receipt.media_kind,
            "cache_chat_id": receipt.chat_id,
            "cache_message_id": receipt.message_id,
            "file_size_bytes": artifact.file_size_bytes,
            "source_track_source_id": source_id,
            "source_provider": artifact.source_provider,
            "source_provider_track_id": artifact.source_provider_track_id,
            "operation": artifact.operation,
            "transcoded": artifact.transcoded,
            "source_codec": artifact.source_codec,
            "source_container": artifact.source_container,
            "source_bitrate_kbps": artifact.source_bitrate_kbps,
            "output_codec": artifact.output_codec,
            "output_container": artifact.output_container,
            "output_bitrate_kbps": artifact.output_bitrate_kbps,
            "sample_rate_hz": artifact.sample_rate_hz,
            "bit_depth": artifact.bit_depth,
            "channels": artifact.channels,
            "duration_ms": artifact.duration_ms,
            "encoder": artifact.encoder,
            "last_used_at": None,
            "status": TelegramCacheStatus.ACTIVE,
            "invalidated_at": None,
            "invalid_reason_code": None,
            "created_at": now,
            "updated_at": now,
        }
        statement = sqlite_insert(TelegramFileCache).values(**values)
        update_values = dict(values)
        update_values.pop("created_at")
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    TelegramFileCache.telegram_bot_id,
                    TelegramFileCache.track_id,
                    TelegramFileCache.quality_profile,
                ],
                set_=update_values,
            )
        )
        await self._session.flush()
        cached = await self.get_active(
            telegram_bot_id=receipt.telegram_bot_id,
            track_id=track_id,
            quality_profile=quality_profile,
        )
        if cached is None:
            raise RuntimeError("cache upsert did not produce an active row")
        return cached

    async def invalidate(
        self, cache_id: int, *, reason_code: str | None, now: datetime
    ) -> TelegramFileCache | None:
        await self._session.execute(
            update(TelegramFileCache)
            .where(TelegramFileCache.id == cache_id)
            .values(
                status=TelegramCacheStatus.INVALID,
                invalidated_at=now,
                invalid_reason_code=reason_code,
                updated_at=now,
            )
        )
        return await self.get(cache_id)

    async def mark_used(self, cache_id: int, *, now: datetime) -> bool:
        result = await self._session.execute(
            update(TelegramFileCache)
            .where(
                TelegramFileCache.id == cache_id,
                TelegramFileCache.status == TelegramCacheStatus.ACTIVE,
            )
            .values(last_used_at=now, updated_at=now)
        )
        return bool(getattr(result, "rowcount", 0))
