"""Provider-independent track persistence operations."""

from __future__ import annotations

from datetime import date
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.models import Track


class TrackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_track_by_id(self, track_id: int) -> Track | None:
        return await self._session.get(Track, track_id)

    async def get_track_by_isrc(self, isrc: str) -> Track | None:
        return cast(
            Track | None,
            await self._session.scalar(
                select(Track).where(Track.isrc == self._normalize_isrc(isrc))
            ),
        )

    async def create_track(
        self,
        *,
        isrc: str | None = None,
        title: str | None = None,
        artist: str | None = None,
        album: str | None = None,
        duration_ms: int | None = None,
        release_date: date | None = None,
        explicit: bool | None = None,
    ) -> Track:
        track = Track(
            isrc=self._normalize_isrc(isrc) if isrc else None,
            title=title,
            artist=artist,
            album=album,
            duration_ms=duration_ms,
            release_date=release_date,
            explicit=explicit,
        )
        self._session.add(track)
        await self._session.flush()
        return track

    async def delete_track(self, track: Track) -> None:
        await self._session.delete(track)
        await self._session.flush()

    @staticmethod
    def _normalize_isrc(isrc: str) -> str:
        return isrc.strip().upper()
