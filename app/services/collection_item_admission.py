"""Provider-neutral admission of one durable collection occurrence."""

from __future__ import annotations

from typing import Protocol

from app.core.enums import MusicProviderName
from app.core.provider_resolution import CanonicalMediaIdentity, CanonicalTrackAdmission
from app.services.track_resolution import ResolveTrackService


class CollectionItemAdmissionResolver(Protocol):
    """Resolve collection discovery identity to a canonical admission identity."""

    async def resolve(
        self,
        *,
        collection_provider: MusicProviderName,
        provider_media_id: str,
    ) -> CanonicalTrackAdmission: ...


class ResolveTrackCollectionItemAdmissionResolver:
    """Adapter to the existing canonical resolver; never selects a downloader."""

    def __init__(self, resolver: ResolveTrackService) -> None:
        self._resolver = resolver

    async def resolve(
        self,
        *,
        collection_provider: MusicProviderName,
        provider_media_id: str,
    ) -> CanonicalTrackAdmission:
        result = await self._resolver.resolve_provider_track(
            collection_provider, provider_media_id, discover=True
        )
        track = result.track
        identity = CanonicalMediaIdentity.from_values(
            title=track.title or "Unknown",
            artist=track.artist or "Unknown",
            album=track.album,
            isrc=track.isrc,
            duration_ms=track.duration_ms,
        )
        return CanonicalTrackAdmission(track.id, identity)


__all__ = [
    "CollectionItemAdmissionResolver",
    "ResolveTrackCollectionItemAdmissionResolver",
]
