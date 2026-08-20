"""Focused persistence operations for the deep-link registry."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import DeepLinkStatus, DeepLinkTargetType, MusicProviderName
from app.storage.models import DeepLinkRegistryEntry


class DeepLinkRegistryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_token(self, telegram_bot_id: int, token: str) -> DeepLinkRegistryEntry | None:
        return cast(
            DeepLinkRegistryEntry | None,
            await self._session.scalar(
                select(DeepLinkRegistryEntry).where(
                    DeepLinkRegistryEntry.telegram_bot_id == telegram_bot_id,
                    DeepLinkRegistryEntry.token == token,
                )
            ),
        )

    async def get_active_by_token(
        self, telegram_bot_id: int, token: str
    ) -> DeepLinkRegistryEntry | None:
        entry = await self.get_by_token(telegram_bot_id, token)
        return entry if entry is not None and entry.status is DeepLinkStatus.ACTIVE else None

    async def get_by_idempotency_key(
        self, telegram_bot_id: int, idempotency_key: str
    ) -> DeepLinkRegistryEntry | None:
        return cast(
            DeepLinkRegistryEntry | None,
            await self._session.scalar(
                select(DeepLinkRegistryEntry).where(
                    DeepLinkRegistryEntry.telegram_bot_id == telegram_bot_id,
                    DeepLinkRegistryEntry.idempotency_key == idempotency_key,
                )
            ),
        )

    async def create(
        self,
        *,
        telegram_bot_id: int,
        token: str,
        target_type: DeepLinkTargetType,
        request_fingerprint: str,
        idempotency_key: str | None,
        track_id: int | None = None,
        album_provider: MusicProviderName | None = None,
        album_provider_id: str | None = None,
    ) -> DeepLinkRegistryEntry:
        entry = DeepLinkRegistryEntry(
            telegram_bot_id=telegram_bot_id,
            token=token,
            target_type=target_type,
            track_id=track_id,
            album_provider=album_provider,
            album_provider_id=album_provider_id,
            status=DeepLinkStatus.ACTIVE,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def revoke_by_token(
        self, telegram_bot_id: int, token: str, *, now: datetime
    ) -> tuple[DeepLinkRegistryEntry | None, bool]:
        result = await self._session.execute(
            update(DeepLinkRegistryEntry)
            .where(
                DeepLinkRegistryEntry.telegram_bot_id == telegram_bot_id,
                DeepLinkRegistryEntry.token == token,
                DeepLinkRegistryEntry.status == DeepLinkStatus.ACTIVE,
            )
            .values(status=DeepLinkStatus.REVOKED, revoked_at=now, updated_at=now)
        )
        entry = await self.get_by_token(telegram_bot_id, token)
        return entry, cast(CursorResult[object], result).rowcount > 0
