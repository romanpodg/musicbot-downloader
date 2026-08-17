"""Track source persistence operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import MusicProviderName
from app.storage.models import TrackSource


@dataclass(frozen=True, slots=True)
class UpsertSourceResult:
    source: TrackSource
    created: bool


class TrackSourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_source(
        self, provider: MusicProviderName, provider_track_id: str
    ) -> TrackSource | None:
        return cast(
            TrackSource | None,
            await self._session.scalar(
                select(TrackSource).where(
                    TrackSource.provider == provider,
                    TrackSource.provider_track_id == provider_track_id,
                )
            ),
        )

    async def get_sources_for_track(self, track_id: int) -> list[TrackSource]:
        result = await self._session.scalars(
            select(TrackSource).where(TrackSource.track_id == track_id).order_by(TrackSource.id)
        )
        return list(result)

    async def upsert_source(
        self,
        *,
        track_id: int,
        provider: MusicProviderName,
        provider_track_id: str,
        url: str | None,
        provider_metadata: dict[str, Any] | None = None,
    ) -> UpsertSourceResult:
        source = await self.get_source(provider, provider_track_id)
        if source is None:
            source = TrackSource(
                track_id=track_id,
                provider=provider,
                provider_track_id=provider_track_id,
                url=url,
                provider_metadata=provider_metadata or {},
            )
            self._session.add(source)
            await self._session.flush()
            return UpsertSourceResult(source=source, created=True)

        source.track_id = track_id
        source.url = url
        source.provider_metadata = provider_metadata or {}
        await self._session.flush()
        return UpsertSourceResult(source=source, created=False)
