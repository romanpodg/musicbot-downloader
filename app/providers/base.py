"""Narrow provider contract required by the current metadata stage."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.core.enums import BatchSourceType, MusicProviderName, ProviderRuntimeStatus
from app.core.models import (
    AlbumSnapshot,
    NormalizedTrackMetadata,
    ProviderCapabilities,
    ProviderMediaCapabilities,
    ProviderSourceCheck,
    ResolvedCollection,
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


@dataclass(frozen=True, slots=True)
class PlaylistReference:
    provider: MusicProviderName
    provider_playlist_id: str
    source_url: str


MediaReference = TrackReference | AlbumReference | PlaylistReference


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
        """Classify a safe URL as a Track, Album, or Playlist input."""

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

    async def get_playlist(self, url: str) -> ResolvedCollection:
        from app.core.exceptions import UnsupportedAlbum

        raise UnsupportedAlbum()

    async def get_playlist_by_id(
        self, provider: MusicProviderName, provider_playlist_id: str
    ) -> ResolvedCollection:
        from app.core.exceptions import UnsupportedAlbum

        raise UnsupportedAlbum()

    async def resolve_collection(
        self, source_type: BatchSourceType, source_reference: str
    ) -> ResolvedCollection:
        """Resolve one immutable album/playlist membership snapshot."""
        from app.core.enums import BatchSourceType

        if source_type is BatchSourceType.ALBUM:
            album = await self.get_album(source_reference)
            from app.core.models import ResolvedCollectionItem

            return ResolvedCollection(
                source_type=source_type,
                provider=album.provider,
                collection_id=album.provider_album_id,
                source_reference=album.source_url,
                title=album.title,
                creator=album.artist,
                items=tuple(
                    ResolvedCollectionItem(
                        position=t.position,
                        provider_media_id=t.provider_track_id,
                        title=t.title,
                        artist=t.artist,
                        duration_ms=t.duration_ms,
                        source_reference=None,
                    )
                    for t in album.tracks
                ),
            )
        if source_type is BatchSourceType.PLAYLIST:
            return await self.get_playlist(source_reference)
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

    async def list_provider_accounts(self, provider: MusicProviderName) -> tuple[str, ...]:
        """Return sanitized durable account identifiers for Stage 25 selection."""
        return ()

    async def close(self) -> None:
        """Release provider-owned resources, if any."""
        return None
