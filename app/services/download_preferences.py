"""Application service for durable, validated user download preferences."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from app.core.download_preferences import UserDownloadPreferences
from app.core.enums import DeliveryMode, FormatPreference, QualityPreference
from app.storage import Database
from app.storage.models.base import utc_now


class UserDownloadPreferencesService:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def get_for_user(self, user_id: int) -> UserDownloadPreferences:
        async with self._database.transaction() as repositories:
            return await repositories.download_preferences.get_effective(user_id)

    async def get_for_telegram_user(self, telegram_id: int) -> UserDownloadPreferences:
        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(telegram_id)
            if user is None:
                raise ValueError("Telegram user must be observed first")
            return await repositories.download_preferences.get_effective(user.id)

    async def update_for_telegram_user(
        self, telegram_id: int, **kwargs: object
    ) -> UserDownloadPreferences:
        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(telegram_id)
            if user is None:
                raise ValueError("Telegram user must be observed first")
        return await self.update(user.id, **kwargs)  # type: ignore[arg-type]

    async def update(
        self,
        user_id: int,
        *,
        quality: QualityPreference | None = None,
        format: FormatPreference | None = None,
        delivery_mode: DeliveryMode | None = None,
        embed_metadata: bool | None = None,
        embed_cover: bool | None = None,
        now: datetime | None = None,
    ) -> UserDownloadPreferences:
        current = await self.get_for_user(user_id)
        updated = UserDownloadPreferences(
            user_id=user_id,
            quality=quality if quality is not None else current.quality,
            format=format if format is not None else current.format,
            delivery_mode=delivery_mode if delivery_mode is not None else current.delivery_mode,
            embed_metadata=(
                embed_metadata if embed_metadata is not None else current.embed_metadata
            ),
            embed_cover=embed_cover if embed_cover is not None else current.embed_cover,
        )
        async with self._database.transaction() as repositories:
            record = await repositories.download_preferences.upsert(updated, now=now or utc_now())
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

    async def update_from_mapping(
        self, user_id: int, values: Mapping[str, object]
    ) -> UserDownloadPreferences:
        """Typed boundary for callback/application adapters; raw strings are rejected."""

        allowed = {"quality", "format", "delivery_mode", "embed_metadata", "embed_cover"}
        if set(values) - allowed:
            raise ValueError("unknown download preference")
        kwargs: dict[str, object] = dict(values)
        if "quality" in kwargs and not isinstance(kwargs["quality"], QualityPreference):
            raise TypeError("quality must be QualityPreference")
        if "format" in kwargs and not isinstance(kwargs["format"], FormatPreference):
            raise TypeError("format must be FormatPreference")
        if "delivery_mode" in kwargs and not isinstance(kwargs["delivery_mode"], DeliveryMode):
            raise TypeError("delivery mode must be DeliveryMode")
        return await self.update(user_id, **kwargs)  # type: ignore[arg-type]

    async def update_quality(
        self, user_id: int, quality: QualityPreference
    ) -> UserDownloadPreferences:
        return await self.update(user_id, quality=quality)

    async def update_format(
        self, user_id: int, format: FormatPreference
    ) -> UserDownloadPreferences:
        return await self.update(user_id, format=format)

    async def update_delivery_mode(
        self, user_id: int, mode: DeliveryMode
    ) -> UserDownloadPreferences:
        return await self.update(user_id, delivery_mode=mode)

    async def set_embed_metadata(self, user_id: int, enabled: bool) -> UserDownloadPreferences:
        return await self.update(user_id, embed_metadata=enabled)

    async def set_embed_cover(self, user_id: int, enabled: bool) -> UserDownloadPreferences:
        return await self.update(user_id, embed_cover=enabled)
