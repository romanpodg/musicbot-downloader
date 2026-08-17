from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from app.core.enums import MusicProviderName
from app.core.exceptions import InvalidTrackUrl, UnsupportedProvider
from app.core.models import TrackSearchRequest
from app.providers.base import ProviderAvailability
from app.providers.onthespot.provider import OnTheSpotProvider


class FakeProcessClient:
    def __init__(self) -> None:
        self.requested_url: str | None = None
        self.requested_search: tuple[str, str, int] | None = None
        self.was_closed = False

    async def availability(self) -> ProviderAvailability:
        return ProviderAvailability(True, version="test")

    async def get_metadata(self, url: str) -> Mapping[str, Any]:
        self.requested_url = url
        return {
            "service": "spotify",
            "item_type": "track",
            "item_id": "0123456789012345678901",
            "metadata": {
                "item_id": "0123456789012345678901",
                "title": "Abracadabra",
                "artists": "Lady Gaga",
                "album_name": "MAYHEM",
                "isrc": "USUM72412581",
                "length": "223000",
                "explicit": False,
                "release_year": "2025",
                "access_token": "must-not-be-persisted",
            },
        }

    async def list_searchable_providers(self) -> list[str]:
        return ["deezer", "not_a_provider", "deezer"]

    async def search_tracks(self, provider: str, query: str, limit: int) -> list[Mapping[str, Any]]:
        self.requested_search = (provider, query, limit)
        return [
            {
                "provider": "deezer",
                "provider_track_id": "123",
                "url": "https://www.deezer.com/track/123?tracking=yes",
                "title": "Search title",
                "artist": "Search artist",
            },
            {"provider": "deezer", "provider_track_id": "bad", "url": "https://bad.invalid"},
        ]

    async def close(self) -> None:
        self.was_closed = True


def _provider(client: FakeProcessClient | None = None) -> OnTheSpotProvider:
    return OnTheSpotProvider(client)  # type: ignore[arg-type]


def test_detects_and_canonicalizes_supported_track_url() -> None:
    reference = _provider().detect_url(
        "https://open.spotify.com/track/0123456789012345678901?si=tracking"
    )
    assert reference.provider is MusicProviderName.SPOTIFY
    assert reference.provider_track_id == "0123456789012345678901"
    assert reference.source_url == "https://open.spotify.com/track/0123456789012345678901"


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@open.spotify.com/track/0123456789012345678901",
        "https://open.spotify.com/track/0123456789012345678901#fragment",
        "https://open.spotify.com:8443/track/0123456789012345678901",
        "ftp://open.spotify.com/track/0123456789012345678901",
        "https://open.spotify.com/album/0123456789012345678901",
        "https://www.deezer.com/album/123",
        "javascript:alert(1)",
    ],
)
def test_rejects_unsafe_non_track_or_malformed_urls(url: str) -> None:
    with pytest.raises(InvalidTrackUrl):
        _provider().detect_url(url)


def test_rejects_unknown_provider() -> None:
    with pytest.raises(UnsupportedProvider):
        _provider().detect_url("https://example.com/track/123")


def test_preserves_required_apple_query_and_removes_tracking() -> None:
    reference = _provider().detect_url(
        "https://music.apple.com/us/album/name/123?i=456&uo=4&at=secret"
    )
    assert reference.provider is MusicProviderName.APPLE_MUSIC
    assert reference.provider_track_id == "456"
    assert reference.source_url == "https://music.apple.com/us/album/name/123?i=456"


def test_preserves_required_youtube_music_query_and_removes_tracking() -> None:
    reference = _provider().detect_url(
        "https://music.youtube.com/watch?v=abc_123-XYZ&list=tracking"
    )
    assert reference.provider is MusicProviderName.YOUTUBE_MUSIC
    assert reference.provider_track_id == "abc_123-XYZ"
    assert reference.source_url == "https://music.youtube.com/watch?v=abc_123-XYZ"


@pytest.mark.asyncio
async def test_passes_only_canonical_url_and_maps_safe_metadata() -> None:
    client = FakeProcessClient()
    provider = _provider(client)
    metadata = await provider.get_metadata(
        "https://open.spotify.com/track/0123456789012345678901?si=tracking"
    )
    assert client.requested_url == "https://open.spotify.com/track/0123456789012345678901"
    assert metadata.title == "Abracadabra"
    assert metadata.duration_ms == 223000
    assert metadata.native.codec is None
    assert metadata.native.container is None
    assert metadata.native.bitrate_kbps is None
    assert "access_token" not in metadata.provider_metadata


@pytest.mark.asyncio
async def test_provider_close_delegates_to_process_client() -> None:
    client = FakeProcessClient()
    provider = _provider(client)
    await provider.close()
    assert client.was_closed is True


@pytest.mark.asyncio
async def test_search_capabilities_and_candidates_are_normalized() -> None:
    client = FakeProcessClient()
    provider = _provider(client)
    searchable = await provider.list_searchable_providers()
    candidates = await provider.search_tracks(
        TrackSearchRequest(MusicProviderName.DEEZER, "Artist Track", 5)
    )
    assert searchable == (MusicProviderName.DEEZER,)
    assert client.requested_search == ("deezer", "Artist Track", 5)
    assert len(candidates) == 1
    assert candidates[0].provider_track_id == "123"
    assert candidates[0].url == "https://www.deezer.com/track/123"
