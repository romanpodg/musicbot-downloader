from __future__ import annotations

import pytest
from sqlalchemy import inspect

from app.core.enums import MusicProviderName
from app.core.exceptions import DatabaseError
from app.storage import Database


@pytest.mark.asyncio
async def test_track_without_isrc_is_valid_and_creation_works(database: Database) -> None:
    async with database.transaction() as repositories:
        track = await repositories.tracks.create_track(title="Unknown release")
        track_id = track.id
    async with database.transaction() as repositories:
        found = await repositories.tracks.get_track_by_id(track_id)
        assert found is not None
        assert found.isrc is None


@pytest.mark.asyncio
async def test_track_lookup_normalizes_isrc(database: Database) -> None:
    async with database.transaction() as repositories:
        await repositories.tracks.create_track(isrc="usabc1234567", title="Track")
    async with database.transaction() as repositories:
        found = await repositories.tracks.get_track_by_isrc(" USABC1234567 ")
        assert found is not None
        assert found.isrc == "USABC1234567"


@pytest.mark.asyncio
async def test_multiple_provider_sources_and_upsert(database: Database) -> None:
    async with database.transaction() as repositories:
        track = await repositories.tracks.create_track(title="Track")
        first = await repositories.track_sources.upsert_source(
            track_id=track.id,
            provider=MusicProviderName.SPOTIFY,
            provider_track_id="spotify-id",
            url="https://open.spotify.com/track/spotify-id",
        )
        second = await repositories.track_sources.upsert_source(
            track_id=track.id,
            provider=MusicProviderName.DEEZER,
            provider_track_id="123",
            url="https://www.deezer.com/track/123",
        )
        repeated = await repositories.track_sources.upsert_source(
            track_id=track.id,
            provider=MusicProviderName.SPOTIFY,
            provider_track_id="spotify-id",
            url="https://open.spotify.com/track/spotify-id?si=new",
        )
        sources = await repositories.track_sources.get_sources_for_track(track.id)
        assert first.created is True
        assert second.created is True
        assert repeated.created is False
        assert len(sources) == 2


@pytest.mark.asyncio
async def test_provider_track_id_pair_is_unique(database: Database) -> None:
    async with database.transaction() as repositories:
        first = await repositories.tracks.create_track(title="One")
        second = await repositories.tracks.create_track(title="Two")
        await repositories.track_sources.upsert_source(
            track_id=first.id,
            provider=MusicProviderName.DEEZER,
            provider_track_id="unique",
            url=None,
        )
        # Repository-level upsert moves the identity instead of duplicating it.
        result = await repositories.track_sources.upsert_source(
            track_id=second.id,
            provider=MusicProviderName.DEEZER,
            provider_track_id="unique",
            url=None,
        )
        assert result.created is False
        assert result.source.track_id == second.id


@pytest.mark.asyncio
async def test_provider_track_id_unique_constraint_exists(database: Database) -> None:
    async with database.engine.connect() as connection:
        constraints = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_unique_constraints("track_sources")
        )
    assert any(
        constraint["column_names"] == ["provider", "provider_track_id"]
        for constraint in constraints
    )


@pytest.mark.asyncio
async def test_foreign_key_rejects_missing_track(database: Database) -> None:
    with pytest.raises(DatabaseError):
        async with database.transaction() as repositories:
            await repositories.track_sources.upsert_source(
                track_id=9999,
                provider=MusicProviderName.TIDAL,
                provider_track_id="missing",
                url=None,
            )


@pytest.mark.asyncio
async def test_deleting_track_cascades_sources(database: Database) -> None:
    async with database.transaction() as repositories:
        track = await repositories.tracks.create_track(title="Delete me")
        track_id = track.id
        await repositories.track_sources.upsert_source(
            track_id=track_id,
            provider=MusicProviderName.QOBUZ,
            provider_track_id="qobuz-id",
            url=None,
        )
    async with database.transaction() as repositories:
        track = await repositories.tracks.get_track_by_id(track_id)
        assert track is not None
        await repositories.tracks.delete_track(track)
    async with database.transaction() as repositories:
        assert await repositories.track_sources.get_sources_for_track(track_id) == []
