"""Persist normalized provider metadata without binding Track to a provider."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.models import NormalizedTrackMetadata
from app.providers.base import MusicProvider
from app.storage.database import Database
from app.storage.models import Track, TrackSource


@dataclass(frozen=True, slots=True)
class ResolveResult:
    metadata: NormalizedTrackMetadata
    track: Track
    source: TrackSource
    track_created: bool
    source_created: bool


class ResolveTrackService:
    def __init__(self, database: Database, provider: MusicProvider) -> None:
        self._database = database
        self._provider = provider

    async def resolve(self, url: str) -> ResolveResult:
        metadata = await self._provider.get_metadata(url)

        async with self._database.transaction() as repositories:
            existing_source = await repositories.track_sources.get_source(
                metadata.provider, metadata.provider_track_id
            )
            track = None
            track_created = False
            if existing_source is not None:
                track = await repositories.tracks.get_track_by_id(existing_source.track_id)
            elif metadata.isrc:
                track = await repositories.tracks.get_track_by_isrc(metadata.isrc)

            if track is None:
                track = await repositories.tracks.create_track(
                    isrc=metadata.isrc,
                    title=metadata.title,
                    artist=metadata.artist,
                    album=metadata.album,
                    duration_ms=metadata.duration_ms,
                    release_date=metadata.release_date,
                    explicit=metadata.explicit,
                )
                track_created = True

            source_result = await repositories.track_sources.upsert_source(
                track_id=track.id,
                provider=metadata.provider,
                provider_track_id=metadata.provider_track_id,
                url=metadata.source_url,
                provider_metadata=metadata.provider_metadata,
            )
            return ResolveResult(
                metadata=metadata,
                track=track,
                source=source_result.source,
                track_created=track_created,
                source_created=source_result.created,
            )
