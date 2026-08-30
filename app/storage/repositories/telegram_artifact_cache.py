"""Atomic persistence for Stage 24 artifact-exact Telegram cache entries."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.telegram_artifact_cache import TelegramCacheKey
from app.storage.models import TelegramArtifactCacheEntry


class TelegramArtifactCacheRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active(self, fingerprint: str) -> TelegramArtifactCacheEntry | None:
        return cast(
            TelegramArtifactCacheEntry | None,
            await self._session.scalar(
                select(TelegramArtifactCacheEntry).where(
                    TelegramArtifactCacheEntry.fingerprint == fingerprint,
                    TelegramArtifactCacheEntry.invalidated_at.is_(None),
                )
            ),
        )

    async def upsert(
        self,
        *,
        key: TelegramCacheKey,
        file_id: str,
        source_delivery_id: int,
        now: datetime,
        file_unique_id: str | None = None,
        file_size: int | None = None,
        mime_type: str | None = None,
    ) -> TelegramArtifactCacheEntry:
        values = {
            "fingerprint": key.fingerprint,
            "provider": key.provider,
            "provider_media_id": key.provider_media_id,
            "effective_quality": key.effective_quality,
            "effective_format": key.effective_format,
            "delivery_mode": key.delivery_mode,
            "embed_metadata": key.embed_metadata,
            "embed_cover": key.embed_cover,
            "artifact_processing_version": key.artifact_processing_version,
            "telegram_file_id": file_id,
            "telegram_file_unique_id": file_unique_id,
            "source_delivery_id": source_delivery_id,
            "file_size": file_size,
            "mime_type": mime_type,
            "hit_count": 0,
            "last_used_at": None,
            "invalidated_at": None,
            "invalidation_reason": None,
            "created_at": now,
            "updated_at": now,
        }
        statement = sqlite_insert(TelegramArtifactCacheEntry).values(**values)
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[TelegramArtifactCacheEntry.fingerprint],
                set_={key: value for key, value in values.items() if key != "created_at"},
            )
        )
        entry = await self.get_active(key.fingerprint)
        if entry is None:
            raise RuntimeError("artifact cache upsert did not produce an active row")
        return entry

    async def mark_used(self, entry_id: int, now: datetime) -> bool:
        result = await self._session.execute(
            update(TelegramArtifactCacheEntry)
            .where(
                TelegramArtifactCacheEntry.id == entry_id,
                TelegramArtifactCacheEntry.invalidated_at.is_(None),
            )
            .values(
                hit_count=TelegramArtifactCacheEntry.hit_count + 1, last_used_at=now, updated_at=now
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def invalidate(self, entry_id: int, reason: str, now: datetime) -> bool:
        result = await self._session.execute(
            update(TelegramArtifactCacheEntry)
            .where(
                TelegramArtifactCacheEntry.id == entry_id,
                TelegramArtifactCacheEntry.invalidated_at.is_(None),
            )
            .values(invalidated_at=now, invalidation_reason=reason, updated_at=now)
        )
        return bool(getattr(result, "rowcount", 0))

    async def prune(
        self, *, max_entries: int, now: datetime, invalid_before: datetime | None
    ) -> int:
        if invalid_before is not None:
            await self._session.execute(
                delete(TelegramArtifactCacheEntry).where(
                    TelegramArtifactCacheEntry.invalidated_at.is_not(None),
                    TelegramArtifactCacheEntry.invalidated_at < invalid_before,
                )
            )
        rows = list(
            await self._session.scalars(
                select(TelegramArtifactCacheEntry.id)
                .where(TelegramArtifactCacheEntry.invalidated_at.is_(None))
                .order_by(
                    TelegramArtifactCacheEntry.last_used_at.is_(None),
                    TelegramArtifactCacheEntry.last_used_at.asc(),
                    TelegramArtifactCacheEntry.created_at.asc(),
                    TelegramArtifactCacheEntry.id.asc(),
                )
            )
        )
        excess = rows[:-max_entries] if len(rows) > max_entries else []
        if excess:
            await self._session.execute(
                delete(TelegramArtifactCacheEntry).where(TelegramArtifactCacheEntry.id.in_(excess))
            )
        return len(excess)
