"""Narrow provider contract required by the current metadata stage."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.core.enums import MusicProviderName, ProviderRuntimeStatus
from app.core.models import (
    AlbumSnapshot,
    NormalizedTrackMetadata,
    ProviderCapabilities,
    ProviderMediaCapabilities,
    ProviderSourceCheck,
    TrackSearchCandidate,
    TrackSearchRequest,
)


@dataclass(frozen=True, slots=True)
class TrackReference:
    provider: MusicProviderName
    provider_track_id: str
    source_url: str


@dataclass(frozen=True, slots=True)
class AlbumReference:
    provider: MusicProviderName
    provider_album_id: str
    source_url: str


MediaReference = TrackReference | AlbumReference


@dataclass(frozen=True, slots=True)
class ProviderAvailability:
    available: bool
    version: str | None = None
    detail: str | None = None


class MusicProvider(ABC):
    @abstractmethod
    async def availability(self) -> ProviderAvailability:
        """Report whether the adapter dependency can be initialized."""

    @abstractmethod
    def detect_url(self, url: str) -> TrackReference:
        """Validate a single-track URL and return its provider identity."""

    async def classify_url(self, url: str) -> MediaReference:
        """Classify a safe URL as a Track or Album input."""

        return self.detect_url(url)

    async def get_album(self, url: str) -> AlbumSnapshot:
        """Resolve one provider-release snapshot without canonical album merging."""

        from app.core.exceptions import UnsupportedAlbum

        raise UnsupportedAlbum()

    async def get_album_by_id(
        self, provider: MusicProviderName, provider_album_id: str
    ) -> AlbumSnapshot:
        """Resolve a durable provider-release identity."""

        from app.core.exceptions import UnsupportedAlbum

        raise UnsupportedAlbum()

    async def get_track_metadata(
        self, provider: MusicProviderName, provider_track_id: str
    ) -> NormalizedTrackMetadata:
        """Resolve one track from stable provider identity."""

        from app.core.exceptions import UnsupportedProvider

        raise UnsupportedProvider()

    @abstractmethod
    async def get_metadata(self, url: str) -> NormalizedTrackMetadata:
        """Resolve and normalize metadata for one track URL."""

    @abstractmethod
    async def list_searchable_providers(self) -> tuple[MusicProviderName, ...]:
        """Return providers whose initialized accounts can currently search."""

    @abstractmethod
    async def search_tracks(self, request: TrackSearchRequest) -> list[TrackSearchCandidate]:
        """Return a bounded set of lightweight track candidates."""

    def provider_capabilities(self, provider: MusicProviderName) -> ProviderCapabilities:
        """Return implementation capabilities without implying runtime readiness."""

        return ProviderCapabilities(
            metadata_supported=False,
            search_supported=False,
            download_supported=False,
            requires_auth=None,
            media=ProviderMediaCapabilities(known=False),
        )

    async def check_source(
        self, provider: MusicProviderName, provider_track_id: str
    ) -> ProviderSourceCheck:
        """Check future download readiness without downloading media."""

        return ProviderSourceCheck(
            ProviderRuntimeStatus.UNSUPPORTED,
            error_code="provider_not_downloadable",
        )

    async def close(self) -> None:
        """Release provider-owned resources, if any."""
        return None
