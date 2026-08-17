from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import func, select

from app.core.enums import MusicProviderName
from app.core.models import NormalizedTrackMetadata
from app.providers.base import MusicProvider, ProviderAvailability, TrackReference
from app.services.track_resolution import ResolveTrackService
from app.storage import Database
from app.storage.models import Track, TrackSource


class StubProvider(MusicProvider):
    def __init__(self, metadata: NormalizedTrackMetadata) -> None:
        self.metadata = metadata

    async def availability(self) -> ProviderAvailability:
        return ProviderAvailability(True)

    def detect_url(self, url: str) -> TrackReference:
        return TrackReference(
            self.metadata.provider,
            self.metadata.provider_track_id,
            self.metadata.source_url,
        )

    async def get_metadata(self, url: str) -> NormalizedTrackMetadata:
        await asyncio.sleep(0)
        return self.metadata


def _metadata(**changes: Any) -> NormalizedTrackMetadata:
    baseline = NormalizedTrackMetadata(
        provider=MusicProviderName.SPOTIFY,
        provider_track_id="provider-id",
        source_url="https://open.spotify.com/track/0123456789012345678901",
        title="Track",
        artist="Artist",
        isrc="USABC1234567",
    )
    return replace(baseline, **changes)


@pytest.mark.asyncio
async def test_repeated_resolve_is_idempotent(database: Database) -> None:
    provider = StubProvider(_metadata())
    service = ResolveTrackService(database, provider)
    first = await service.resolve(provider.metadata.source_url)
    second = await service.resolve(provider.metadata.source_url)
    assert first.track_created is True
    assert first.source_created is True
    assert second.track_created is False
    assert second.source_created is False
    assert second.track.id == first.track.id
    assert second.source.id == first.source.id


@pytest.mark.asyncio
async def test_stage_two_does_not_merge_different_sources_by_isrc(database: Database) -> None:
    spotify = StubProvider(_metadata())
    deezer = StubProvider(
        _metadata(
            provider=MusicProviderName.DEEZER,
            provider_track_id="123",
            source_url="https://www.deezer.com/track/123",
        )
    )
    first = await ResolveTrackService(database, spotify).resolve(spotify.metadata.source_url)
    second = await ResolveTrackService(database, deezer).resolve(deezer.metadata.source_url)
    assert first.track.id != second.track.id
    async with database.transaction() as repositories:
        candidates = await repositories.tracks.get_tracks_by_isrc("USABC1234567")
        assert [candidate.id for candidate in candidates] == [first.track.id, second.track.id]


@pytest.mark.asyncio
async def test_existing_track_is_enriched_without_overwriting_canonical_data(
    database: Database,
) -> None:
    provider = StubProvider(_metadata(title=None, artist="Established", album=None, isrc=None))
    service = ResolveTrackService(database, provider)
    first = await service.resolve(provider.metadata.source_url)
    provider.metadata = _metadata(
        title="Discovered",
        artist="Conflicting",
        album="Album",
        isrc="USNEW1234567",
        provider_metadata={"release_year": "2025", "access_token": "secret"},
    )
    second = await service.resolve(provider.metadata.source_url)
    provider.metadata = _metadata(
        title=None,
        artist=None,
        album=None,
        isrc="USOTHER12345",
        provider_metadata={"release_year": None},
    )
    third = await service.resolve(provider.metadata.source_url)

    assert first.track.id == second.track.id == third.track.id
    assert third.track.title == "Discovered"
    assert third.track.artist == "Established"
    assert third.track.album == "Album"
    assert third.track.isrc == "USNEW1234567"
    assert third.source.provider_metadata == {"release_year": "2025"}


@pytest.mark.asyncio
async def test_concurrent_same_source_is_idempotent_without_orphan_track(
    database: Database,
) -> None:
    metadata = _metadata()
    first_service = ResolveTrackService(database, StubProvider(metadata))
    second_service = ResolveTrackService(database, StubProvider(metadata))
    first, second = await asyncio.gather(
        first_service.resolve(metadata.source_url),
        second_service.resolve(metadata.source_url),
    )
    assert first.track.id == second.track.id
    assert first.source.id == second.source.id
    async with database.engine.connect() as connection:
        track_count = await connection.scalar(select(func.count()).select_from(Track))
        source_count = await connection.scalar(select(func.count()).select_from(TrackSource))
    assert track_count == 1
    assert source_count == 1
