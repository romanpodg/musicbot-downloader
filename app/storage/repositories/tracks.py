"""Provider-independent track persistence operations."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.track_identity import normalize_duration_ms, normalize_isrc, normalize_title_artist
from app.storage.models import Track

MAX_DATABASE_CANDIDATES = 50


class TrackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_track_by_id(self, track_id: int) -> Track | None:
        return await self._session.get(Track, track_id)

    async def get_tracks_by_isrc(self, isrc: str) -> list[Track]:
        """Return a bounded candidate set plus one truncation sentinel row."""

        normalized = normalize_isrc(isrc)
        if normalized is None:
            return []
        result = await self._session.scalars(
            select(Track)
            .where(Track.isrc == normalized)
            .order_by(Track.id)
            .limit(MAX_DATABASE_CANDIDATES + 1)
        )
        return list(result)

    async def get_tracks_by_normalized_identity(
        self, normalized_title: str, normalized_artist: str
    ) -> list[Track]:
        result = await self._session.scalars(
            select(Track)
            .where(
                Track.normalized_title == normalized_title,
                Track.normalized_artist == normalized_artist,
            )
            .order_by(Track.id)
            .limit(MAX_DATABASE_CANDIDATES + 1)
        )
        return list(result)

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
        normalized_title, normalized_artist, _ = normalize_title_artist(title, artist)
        track = Track(
            isrc=normalize_isrc(isrc),
            title=title,
            artist=artist,
            normalized_title=normalized_title,
            normalized_artist=normalized_artist,
            album=album,
            duration_ms=normalize_duration_ms(duration_ms),
            release_date=release_date,
            explicit=explicit,
        )
        self._session.add(track)
        await self._session.flush()
        return track

    async def enrich_missing(
        self,
        track: Track,
        *,
        isrc: str | None,
        title: str | None,
        artist: str | None,
        album: str | None,
        duration_ms: int | None,
        release_date: date | None,
        explicit: bool | None,
    ) -> Track:
        """Fill null canonical fields without replacing established metadata."""

        incoming: dict[str, object | None] = {
            "isrc": normalize_isrc(isrc),
            "title": title,
            "artist": artist,
            "album": album,
            "duration_ms": normalize_duration_ms(duration_ms),
            "release_date": release_date,
            "explicit": explicit,
        }
        changed = False
        for field, value in incoming.items():
            if getattr(track, field) is None and value is not None:
                setattr(track, field, value)
                changed = True
        if changed:
            track.normalized_title, track.normalized_artist, _ = normalize_title_artist(
                track.title, track.artist
            )
            await self._session.flush()
        return track

    async def delete_track(self, track: Track) -> None:
        await self._session.delete(track)
        await self._session.flush()
