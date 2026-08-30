"""Persistence adapter for Stage 22 user preferences."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.download_preferences import UserDownloadPreferences, default_preferences
from app.storage.models.download_preferences import UserDownloadPreferencesRecord


class UserDownloadPreferencesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: int) -> UserDownloadPreferencesRecord | None:
        return await self._session.get(UserDownloadPreferencesRecord, user_id)

    async def upsert(
        self, preferences: UserDownloadPreferences, *, now: datetime
    ) -> UserDownloadPreferencesRecord:
        values: dict[str, Any] = {
            "user_id": preferences.user_id,
            "quality": preferences.quality,
            "format": preferences.format,
            "delivery_mode": preferences.delivery_mode,
            "embed_metadata": preferences.embed_metadata,
            "embed_cover": preferences.embed_cover,
            "created_at": now,
            "updated_at": now,
        }
        await self._session.execute(
            sqlite_insert(UserDownloadPreferencesRecord)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[UserDownloadPreferencesRecord.user_id],
                set_={key: values[key] for key in values if key not in {"user_id", "created_at"}},
            )
        )
        record = await self.get(preferences.user_id)
        if record is None:
            raise RuntimeError("preference upsert failed")
        return record

    async def get_effective(self, user_id: int) -> UserDownloadPreferences:
        record = await self.get(user_id)
        if record is None:
            return default_preferences(user_id)
        return UserDownloadPreferences(
            user_id=record.user_id,
            quality=record.quality,
            format=record.format,
            delivery_mode=record.delivery_mode,
            embed_metadata=record.embed_metadata,
            embed_cover=record.embed_cover,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
