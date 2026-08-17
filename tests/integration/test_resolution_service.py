from __future__ import annotations

import pytest

from app.core.enums import MusicProviderName
from app.core.models import NormalizedTrackMetadata
from app.providers.base import MusicProvider, ProviderAvailability, TrackReference
from app.services.track_resolution import ResolveTrackService
from app.storage import Database


class StubProvider(MusicProvider):
    async def availability(self) -> ProviderAvailability:
        return ProviderAvailability(True)

    def detect_url(self, url: str) -> TrackReference:
        return TrackReference(MusicProviderName.SPOTIFY, "provider-id", url)

    async def get_metadata(self, url: str) -> NormalizedTrackMetadata:
        return NormalizedTrackMetadata(
            provider=MusicProviderName.SPOTIFY,
            provider_track_id="provider-id",
            source_url=url,
            title="Track",
            artist="Artist",
            isrc="USABC1234567",
        )


@pytest.mark.asyncio
async def test_repeated_resolve_is_idempotent(database: Database) -> None:
    service = ResolveTrackService(database, StubProvider())
    url = "https://open.spotify.com/track/0123456789012345678901"
    first = await service.resolve(url)
    second = await service.resolve(url)
    assert first.track_created is True
    assert first.source_created is True
    assert second.track_created is False
    assert second.source_created is False
    assert second.track.id == first.track.id
    assert second.source.id == first.source.id
