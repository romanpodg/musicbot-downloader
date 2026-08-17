from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import func, select

from app.core.enums import MusicProviderName, ProviderDiscoveryStatus, TrackMatchDecision
from app.core.exceptions import ProviderAuthenticationError
from app.core.models import NormalizedTrackMetadata, TrackSearchCandidate, TrackSearchRequest
from app.providers.base import MusicProvider, ProviderAvailability, TrackReference
from app.services.track_resolution import ResolveTrackService
from app.storage import Database
from app.storage.models import Track, TrackSource


class StubProvider(MusicProvider):
    def __init__(self, metadata: NormalizedTrackMetadata) -> None:
        self.metadata = metadata
        self.metadata_by_url: dict[str, NormalizedTrackMetadata] = {}
        self.searchable: tuple[MusicProviderName, ...] = ()
        self.search_results: dict[MusicProviderName, list[TrackSearchCandidate]] = {}
        self.auth_required: set[MusicProviderName] = set()
        self.unexpected_failure: set[MusicProviderName] = set()

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
        return self.metadata_by_url.get(url, self.metadata)

    async def list_searchable_providers(self) -> tuple[MusicProviderName, ...]:
        return self.searchable

    async def search_tracks(self, request: TrackSearchRequest) -> list[TrackSearchCandidate]:
        if request.target_provider in self.auth_required:
            raise ProviderAuthenticationError()
        if request.target_provider in self.unexpected_failure:
            raise RuntimeError("simulated provider bug")
        return self.search_results.get(request.target_provider, [])[: request.limit]


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
async def test_stage_three_merges_compatible_sources_by_isrc(database: Database) -> None:
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
    assert first.track.id == second.track.id
    assert second.input_decision is TrackMatchDecision.MATCHED
    async with database.transaction() as repositories:
        candidates = await repositories.tracks.get_tracks_by_isrc("USABC1234567")
        assert [candidate.id for candidate in candidates] == [first.track.id]


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


@pytest.mark.asyncio
async def test_no_isrc_without_duration_is_ambiguous_and_creates_separate_track(
    database: Database,
) -> None:
    first_provider = StubProvider(_metadata(isrc=None, duration_ms=None))
    second_provider = StubProvider(
        _metadata(
            provider=MusicProviderName.DEEZER,
            provider_track_id="123",
            source_url="https://www.deezer.com/track/123",
            isrc=None,
            duration_ms=None,
        )
    )
    first = await ResolveTrackService(database, first_provider).resolve(
        first_provider.metadata.source_url
    )
    second = await ResolveTrackService(database, second_provider).resolve(
        second_provider.metadata.source_url
    )
    assert second.input_decision is TrackMatchDecision.AMBIGUOUS
    assert second.track.id != first.track.id


@pytest.mark.asyncio
async def test_duplicate_compatible_isrc_candidates_are_ambiguous(database: Database) -> None:
    async with database.transaction() as repositories:
        await repositories.tracks.create_track(
            isrc="USABC1234567", title="Track", artist="Artist", duration_ms=180_000
        )
        await repositories.tracks.create_track(
            isrc="USABC1234567", title="Track", artist="Artist", duration_ms=180_500
        )
    provider = StubProvider(_metadata(duration_ms=180_100))
    result = await ResolveTrackService(database, provider).resolve(provider.metadata.source_url)
    assert result.input_decision is TrackMatchDecision.AMBIGUOUS
    assert result.track_created is True


@pytest.mark.asyncio
async def test_oversized_database_candidate_set_is_ambiguous(database: Database) -> None:
    async with database.transaction() as repositories:
        for index in range(51):
            await repositories.tracks.create_track(
                isrc="USABC1234567",
                title="Track",
                artist="Artist",
                duration_ms=180_000 + index,
            )
    provider = StubProvider(_metadata(duration_ms=180_000))
    result = await ResolveTrackService(database, provider).resolve(provider.metadata.source_url)
    assert result.input_decision is TrackMatchDecision.AMBIGUOUS
    assert result.track_created is True


@pytest.mark.asyncio
async def test_discovery_verifies_full_metadata_and_attaches_match(database: Database) -> None:
    input_metadata = _metadata(duration_ms=180_000, album="Single")
    provider = StubProvider(input_metadata)
    provider.searchable = (MusicProviderName.SPOTIFY, MusicProviderName.DEEZER)
    candidate_url = "https://www.deezer.com/track/123"
    provider.search_results[MusicProviderName.DEEZER] = [
        TrackSearchCandidate(
            MusicProviderName.DEEZER,
            "123",
            candidate_url,
            title="Untrusted search title",
            artist="Wrong search artist",
        )
    ]
    provider.metadata_by_url[candidate_url] = _metadata(
        provider=MusicProviderName.DEEZER,
        provider_track_id="123",
        source_url=candidate_url,
        duration_ms=181_000,
        album="Compilation",
    )
    result = await ResolveTrackService(database, provider).resolve(
        input_metadata.source_url, discover=True
    )
    assert result.discoveries[0].status is ProviderDiscoveryStatus.MATCHED
    async with database.transaction() as repositories:
        sources = await repositories.track_sources.get_sources_for_track(result.track.id)
    assert {(source.provider, source.provider_track_id) for source in sources} == {
        (MusicProviderName.SPOTIFY, "provider-id"),
        (MusicProviderName.DEEZER, "123"),
    }
    repeated = await ResolveTrackService(database, provider).resolve(
        input_metadata.source_url, discover=True
    )
    assert repeated.discoveries[0].status is ProviderDiscoveryStatus.MATCHED
    async with database.transaction() as repositories:
        assert len(await repositories.track_sources.get_sources_for_track(result.track.id)) == 2


@pytest.mark.asyncio
async def test_ambiguous_discovery_candidate_is_not_persisted(database: Database) -> None:
    provider = StubProvider(_metadata(duration_ms=180_000))
    candidate_url = "https://www.deezer.com/track/123"
    provider.searchable = (MusicProviderName.DEEZER,)
    provider.search_results[MusicProviderName.DEEZER] = [
        TrackSearchCandidate(MusicProviderName.DEEZER, "123", candidate_url)
    ]
    provider.metadata_by_url[candidate_url] = _metadata(
        provider=MusicProviderName.DEEZER,
        provider_track_id="123",
        source_url=candidate_url,
        isrc=None,
        duration_ms=None,
    )
    result = await ResolveTrackService(database, provider).resolve(
        provider.metadata.source_url, discover=True
    )
    assert result.discoveries[0].status is ProviderDiscoveryStatus.AMBIGUOUS
    async with database.transaction() as repositories:
        assert await repositories.track_sources.get_source(MusicProviderName.DEEZER, "123") is None


@pytest.mark.asyncio
async def test_discovery_does_not_reassign_existing_source(database: Database) -> None:
    provider = StubProvider(_metadata(duration_ms=180_000))
    candidate_url = "https://www.deezer.com/track/123"
    provider.searchable = (MusicProviderName.DEEZER,)
    provider.search_results[MusicProviderName.DEEZER] = [
        TrackSearchCandidate(MusicProviderName.DEEZER, "123", candidate_url)
    ]
    provider.metadata_by_url[candidate_url] = _metadata(
        provider=MusicProviderName.DEEZER,
        provider_track_id="123",
        source_url=candidate_url,
        duration_ms=180_000,
    )
    async with database.transaction() as repositories:
        other = await repositories.tracks.create_track(title="Other")
        await repositories.track_sources.upsert_source(
            track_id=other.id,
            provider=MusicProviderName.DEEZER,
            provider_track_id="123",
            url=candidate_url,
        )
        other_id = other.id
    result = await ResolveTrackService(database, provider).resolve(
        provider.metadata.source_url, discover=True
    )
    assert result.discoveries[0].status is ProviderDiscoveryStatus.IDENTITY_CONFLICT
    async with database.transaction() as repositories:
        source = await repositories.track_sources.get_source(MusicProviderName.DEEZER, "123")
    assert source is not None
    assert source.track_id == other_id


@pytest.mark.asyncio
async def test_provider_failure_does_not_abort_other_discovery(database: Database) -> None:
    provider = StubProvider(_metadata(duration_ms=180_000))
    provider.searchable = (MusicProviderName.TIDAL, MusicProviderName.DEEZER)
    provider.auth_required.add(MusicProviderName.TIDAL)
    provider.search_results[MusicProviderName.DEEZER] = []
    result = await ResolveTrackService(database, provider).resolve(
        provider.metadata.source_url, discover=True
    )
    assert [item.status for item in result.discoveries] == [
        ProviderDiscoveryStatus.AUTH_REQUIRED,
        ProviderDiscoveryStatus.NO_MATCH,
    ]


@pytest.mark.asyncio
async def test_unexpected_provider_failure_is_structured_and_isolated(database: Database) -> None:
    provider = StubProvider(_metadata(duration_ms=180_000))
    provider.searchable = (MusicProviderName.QOBUZ, MusicProviderName.DEEZER)
    provider.unexpected_failure.add(MusicProviderName.QOBUZ)
    result = await ResolveTrackService(database, provider).resolve(
        provider.metadata.source_url, discover=True
    )
    assert [item.status for item in result.discoveries] == [
        ProviderDiscoveryStatus.ERROR,
        ProviderDiscoveryStatus.NO_MATCH,
    ]
