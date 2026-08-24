from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from app.application.search import SearchTracksUseCase
from app.core.enums import MusicProviderName
from app.core.exceptions import ProviderAuthenticationError, ProviderUnavailable
from app.core.models import TrackSearchCandidate
from app.core.models import TrackSearchRequest as RuntimeTrackSearchRequest
from app.core.search import TrackSearchRequest
from app.providers.base import MusicProvider
from app.providers.search import TrackSearchProvider
from app.providers.search_adapters import (
    DeezerSearchAdapter,
    SpotifySearchAdapter,
    TidalSearchAdapter,
)
from app.providers.search_mappers import DeezerTrackMapper, SpotifyTrackMapper, TidalTrackMapper
from app.services.track_search import (
    TrackSearchProviderRegistry,
    TrackSearchService,
    TrackSearchUnavailable,
)


@pytest.mark.parametrize(
    ("mapper", "provider"),
    [
        (SpotifyTrackMapper(), MusicProviderName.SPOTIFY),
        (DeezerTrackMapper(), MusicProviderName.DEEZER),
        (TidalTrackMapper(), MusicProviderName.TIDAL),
    ],
)
def test_stage16_provider_mappers_normalize_only_their_own_candidates(
    mapper, provider: MusicProviderName
) -> None:
    candidate = TrackSearchCandidate(
        provider=provider,
        provider_track_id="track-42",
        url=f"https://example.invalid/{provider.value}/track-42",
        title="  Normalized title ",
        artist="  Normalized artist ",
    )

    track = mapper.map(candidate)

    assert track is not None
    assert track.id == f"search:{provider.value}:track-42"
    assert track.title == "Normalized title"
    assert track.artists[0].name == "Normalized artist"
    assert track.provider is provider
    assert track.provider_track_id == "track-42"
    assert track.album is None
    assert track.duration_ms is None
    other_provider = (
        MusicProviderName.DEEZER
        if provider is not MusicProviderName.DEEZER
        else MusicProviderName.SPOTIFY
    )
    assert (
        mapper.map(
            TrackSearchCandidate(
                provider=other_provider,
                provider_track_id="other",
                url="https://example.invalid/other",
                title="Other",
                artist="Other",
            )
        )
        is None
    )


@pytest.mark.parametrize(
    ("adapter_type", "provider"),
    [
        (SpotifySearchAdapter, MusicProviderName.SPOTIFY),
        (DeezerSearchAdapter, MusicProviderName.DEEZER),
        (TidalSearchAdapter, MusicProviderName.TIDAL),
    ],
)
async def test_stage16_adapters_delegate_to_existing_runtime_and_map_candidates(
    adapter_type, provider: MusicProviderName
) -> None:
    runtime = Mock(spec=MusicProvider)
    runtime.search_tracks = AsyncMock(
        return_value=[
            TrackSearchCandidate(
                provider=provider,
                provider_track_id="track-42",
                url=f"https://example.invalid/{provider.value}/track-42",
                title="Title",
                artist="Artist",
            )
        ]
    )
    adapter = adapter_type(runtime)

    result = await adapter.search(TrackSearchRequest("Artist Title", limit=25))

    assert isinstance(adapter, TrackSearchProvider)
    runtime.search_tracks.assert_awaited_once_with(
        RuntimeTrackSearchRequest(provider, "Artist Title", 10)
    )
    assert result[0].provider is provider
    assert result[0].provider_track_id == "track-42"


def test_stage16_registry_registers_each_real_adapter_once() -> None:
    runtime = Mock(spec=MusicProvider)
    registry = TrackSearchProviderRegistry(
        (
            SpotifySearchAdapter(runtime),
            DeezerSearchAdapter(runtime),
            TidalSearchAdapter(runtime),
        )
    )

    assert registry.names == (
        MusicProviderName.SPOTIFY,
        MusicProviderName.DEEZER,
        MusicProviderName.TIDAL,
    )
    assert isinstance(registry.get(MusicProviderName.SPOTIFY), SpotifySearchAdapter)
    assert isinstance(registry.get(MusicProviderName.DEEZER), DeezerSearchAdapter)
    assert isinstance(registry.get(MusicProviderName.TIDAL), TidalSearchAdapter)


async def test_stage16_use_case_continues_after_unavailable_provider_with_real_adapter_layer() -> (
    None
):
    runtime = Mock(spec=MusicProvider)
    runtime.search_tracks = AsyncMock(
        side_effect=[
            ProviderAuthenticationError(),
            [
                TrackSearchCandidate(
                    provider=MusicProviderName.DEEZER,
                    provider_track_id="deezer-42",
                    url="https://example.invalid/deezer-42",
                    title="Deezer title",
                    artist="Deezer artist",
                )
            ],
            ProviderUnavailable(),
        ]
    )
    use_case = SearchTracksUseCase(
        TrackSearchService(
            TrackSearchProviderRegistry(
                (
                    SpotifySearchAdapter(runtime),
                    DeezerSearchAdapter(runtime),
                    TidalSearchAdapter(runtime),
                )
            )
        )
    )

    result = await use_case.execute(TrackSearchRequest("query", limit=5))

    assert [track.provider for track in result.tracks] == [MusicProviderName.DEEZER]
    assert runtime.search_tracks.await_count == 3


async def test_stage16_all_provider_failures_become_normalized_unavailable() -> None:
    runtime = Mock(spec=MusicProvider)
    runtime.search_tracks = AsyncMock(side_effect=ProviderUnavailable())
    use_case = SearchTracksUseCase(
        TrackSearchService(
            TrackSearchProviderRegistry(
                (
                    SpotifySearchAdapter(runtime),
                    DeezerSearchAdapter(runtime),
                    TidalSearchAdapter(runtime),
                )
            )
        )
    )

    with pytest.raises(TrackSearchUnavailable):
        await use_case.execute(TrackSearchRequest("query"))
