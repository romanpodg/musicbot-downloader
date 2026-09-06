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


class RuntimeTrackSearchAdapter(TrackSearchProvider):
    """Delegate a bounded query to one provider and map only safe candidates."""

    def __init__(
        self,
        runtime: MusicProvider,
        provider: MusicProviderName,
        mapper: ProviderTrackMapper | None = None,
    ) -> None:
        self._runtime = runtime
        self._provider = provider
        self._mapper = mapper or ProviderTrackMapper(provider)
        if self._mapper.provider is not provider:
            raise ValueError("search adapter mapper provider mismatch")

    @property
    def provider(self) -> MusicProviderName:
        return self._provider

    async def search(self, request: TrackSearchRequest) -> tuple[Track, ...]:
        candidates = await self._runtime.search_tracks(
            RuntimeTrackSearchRequest(
                target_provider=self.provider,
                query=request.query,
                limit=min(request.limit, _RUNTIME_SEARCH_LIMIT),
            )
        )
        return self._mapper.map_all(candidates)


class SpotifySearchAdapter(RuntimeTrackSearchAdapter):
    """Stage 16 compatibility wrapper; production uses RuntimeTrackSearchAdapter."""

    def __init__(self, runtime: MusicProvider) -> None:
        super().__init__(runtime, MusicProviderName.SPOTIFY, SpotifyTrackMapper())


class DeezerSearchAdapter(RuntimeTrackSearchAdapter):
    """Stage 16 compatibility wrapper; production uses RuntimeTrackSearchAdapter."""

    def __init__(self, runtime: MusicProvider) -> None:
        super().__init__(runtime, MusicProviderName.DEEZER, DeezerTrackMapper())


class TidalSearchAdapter(RuntimeTrackSearchAdapter):
    """Stage 16 compatibility wrapper; production uses RuntimeTrackSearchAdapter."""

    def __init__(self, runtime: MusicProvider) -> None:
        super().__init__(runtime, MusicProviderName.TIDAL, TidalTrackMapper())
