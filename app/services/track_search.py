"""Application-level provider-neutral track search orchestration."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from app.core.enums import MusicProviderName
from app.core.exceptions import ProviderAuthenticationError, ProviderUnavailable
from app.core.search import Track, TrackSearchRequest, TrackSearchResult
from app.providers.search import TrackSearchProvider

logger = logging.getLogger(__name__)


class TrackSearchUnavailable(ProviderUnavailable):
    """No requested search provider could complete this request safely."""


class TrackSearchProviderRegistry:
    """Composition-owned mapping from provider name to a search adapter."""

    def __init__(self, providers: Iterable[TrackSearchProvider] = ()) -> None:
        self._providers: dict[MusicProviderName, TrackSearchProvider] = {}
        for provider in providers:
            self.register(provider)

    @property
    def names(self) -> tuple[MusicProviderName, ...]:
        return tuple(self._providers)

    def register(self, provider: TrackSearchProvider) -> None:
        if provider.provider in self._providers:
            raise ValueError(f"duplicate track search provider: {provider.provider.value}")
        self._providers[provider.provider] = provider

    def get(self, provider: MusicProviderName) -> TrackSearchProvider | None:
        return self._providers.get(provider)


class TrackSearchService:
    """Select registered adapters and return bounded, normalized catalog results."""

    def __init__(self, registry: TrackSearchProviderRegistry) -> None:
        self._registry = registry

    async def search(self, request: TrackSearchRequest) -> TrackSearchResult:
        requested_providers = request.providers or self._registry.names
        if not requested_providers:
            raise TrackSearchUnavailable()

        tracks: list[Track] = []
        seen: set[tuple[MusicProviderName, str]] = set()
        completed_provider = False
        for provider_name in requested_providers:
            provider = self._registry.get(provider_name)
            if provider is None:
                logger.info(
                    "Track search provider is not registered",
                    extra={"provider": provider_name.value},
                )
                continue
            try:
                provider_tracks = await provider.search(request)
            except (ProviderAuthenticationError, ProviderUnavailable):
                logger.info(
                    "Track search provider is unavailable", extra={"provider": provider_name.value}
                )
                continue
            except Exception:
                logger.warning(
                    "Track search provider failed", extra={"provider": provider_name.value}
                )
                continue

            completed_provider = True
            for track in provider_tracks:
                if track.provider is not provider_name:
                    logger.warning(
                        "Track search provider returned a mismatched provider",
                        extra={"provider": provider_name.value},
                    )
                    continue
                identity = (track.provider, track.provider_track_id)
                if identity in seen:
                    continue
                seen.add(identity)
                tracks.append(track)
                if len(tracks) == request.limit:
                    return TrackSearchResult(request.query, tuple(tracks))

        if not completed_provider:
            raise TrackSearchUnavailable()
        return TrackSearchResult(request.query, tuple(tracks))
