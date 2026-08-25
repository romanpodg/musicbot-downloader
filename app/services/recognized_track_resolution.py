"""Adapter from Stage 17 catalog tracks to the existing canonical Track resolver."""

from __future__ import annotations

from app.core.search import Track
from app.services.track_resolution import ResolveTrackService


class RecognizedTrackResolutionAdapter:
    """Preserves Stage 3 identity persistence before Stage 6 queue admission."""

    def __init__(self, resolver: ResolveTrackService) -> None:
        self._resolver = resolver

    async def resolve_track_id(self, track: Track) -> int:
        result = await self._resolver.resolve_provider_track(
            track.provider, track.provider_track_id, discover=True
        )
        return result.track.id
