"""Thin Stage 16 catalog-search adapters over the existing provider runtime."""

from __future__ import annotations

from app.core.enums import MusicProviderName
from app.core.models import TrackSearchRequest as RuntimeTrackSearchRequest
from app.core.search import Track, TrackSearchRequest
from app.providers.base import MusicProvider
from app.providers.search import TrackSearchProvider
from app.providers.search_mappers import (
    DeezerTrackMapper,
    ProviderTrackMapper,
    SpotifyTrackMapper,
    TidalTrackMapper,
)

_RUNTIME_SEARCH_LIMIT = 10


class _RuntimeSearchAdapter(TrackSearchProvider):
    """Delegate a bounded query to one provider and map only safe candidates."""

    mapper: ProviderTrackMapper

    def __init__(self, runtime: MusicProvider) -> None:
        self._runtime = runtime

    @property
    def provider(self) -> MusicProviderName:
        return self.mapper.provider

    async def search(self, request: TrackSearchRequest) -> tuple[Track, ...]:
        candidates = await self._runtime.search_tracks(
            RuntimeTrackSearchRequest(
                target_provider=self.provider,
                query=request.query,
                limit=min(request.limit, _RUNTIME_SEARCH_LIMIT),
            )
        )
        return self.mapper.map_all(candidates)


class SpotifySearchAdapter(_RuntimeSearchAdapter):
    mapper = SpotifyTrackMapper()


class DeezerSearchAdapter(_RuntimeSearchAdapter):
    mapper = DeezerTrackMapper()


class TidalSearchAdapter(_RuntimeSearchAdapter):
    mapper = TidalTrackMapper()
