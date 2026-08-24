from __future__ import annotations

import pytest

from app.application.search import SearchTracksUseCase
from app.application.ux.services.errors import UxErrorMessage, UxErrorService
from app.core.enums import MusicProviderName
from app.core.search import Album, Artist, Track, TrackSearchRequest, TrackSearchResult
from app.providers.search import TrackSearchProvider
from app.services.track_search import (
    TrackSearchProviderRegistry,
    TrackSearchService,
    TrackSearchUnavailable,
)


class FakeSearchProvider(TrackSearchProvider):
    def __init__(
        self,
        provider: MusicProviderName,
        tracks: tuple[Track, ...] = (),
        error: Exception | None = None,
    ) -> None:
        self._provider = provider
        self._tracks = tracks
        self._error = error
        self.requests: list[TrackSearchRequest] = []

    @property
    def provider(self) -> MusicProviderName:
        return self._provider

    async def search(self, request: TrackSearchRequest) -> tuple[Track, ...]:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return self._tracks


def _track(
    provider: MusicProviderName = MusicProviderName.SPOTIFY,
    provider_track_id: str = "provider-track-1",
) -> Track:
    return Track(
        id=f"result:{provider.value}:{provider_track_id}",
        title="One More Time",
        artists=(Artist("Daft Punk", "artist-1"),),
        album=Album("Discovery", "album-1"),
        duration_ms=320_000,
        provider=provider,
        provider_track_id=provider_track_id,
    )


def test_stage15_track_domain_models_are_provider_independent() -> None:
    track = _track()

    assert track.title == "One More Time"
    assert track.artists[0].name == "Daft Punk"
    assert track.album is not None
    assert track.album.title == "Discovery"
    with pytest.raises(ValueError, match="at least one artist"):
        Track("result:empty", "No Artist", (), MusicProviderName.SPOTIFY, "track-2")


def test_stage15_search_request_validation_and_result_shape() -> None:
    request = TrackSearchRequest("  Daft Punk One More Time  ", (MusicProviderName.SPOTIFY,), 5)
    result = TrackSearchResult(request.query, (_track(),))

    assert request.query == "Daft Punk One More Time"
    assert result.tracks[0].provider_track_id == "provider-track-1"
    with pytest.raises(ValueError, match="must not be empty"):
        TrackSearchRequest(" ")
    with pytest.raises(ValueError, match="must be unique"):
        TrackSearchRequest("query", (MusicProviderName.SPOTIFY, MusicProviderName.SPOTIFY))


async def test_stage15_registry_and_service_normalize_results() -> None:
    spotify_track = _track()
    duplicate_track = _track()
    mismatched_track = _track(MusicProviderName.DEEZER, "deezer-track")
    spotify = FakeSearchProvider(
        MusicProviderName.SPOTIFY,
        (spotify_track, duplicate_track, mismatched_track),
    )
    registry = TrackSearchProviderRegistry((spotify,))
    service = TrackSearchService(registry)

    result = await service.search(TrackSearchRequest("Daft Punk", limit=5))

    assert registry.get(MusicProviderName.SPOTIFY) is spotify
    assert registry.names == (MusicProviderName.SPOTIFY,)
    assert result == TrackSearchResult("Daft Punk", (spotify_track,))
    assert spotify.requests == [TrackSearchRequest("Daft Punk", limit=5)]
    with pytest.raises(ValueError, match="duplicate"):
        registry.register(spotify)


async def test_stage15_use_case_and_error_boundary_report_unavailable_search() -> None:
    service = TrackSearchService(TrackSearchProviderRegistry())
    use_case = SearchTracksUseCase(service)

    with pytest.raises(TrackSearchUnavailable):
        await use_case.execute(TrackSearchRequest("Daft Punk"))
    assert (
        UxErrorService().message_name(TrackSearchUnavailable()) is UxErrorMessage.SEARCH_UNAVAILABLE
    )
