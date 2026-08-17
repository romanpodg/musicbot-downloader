from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from app.core.enums import MusicProviderName
from app.core.exceptions import InvalidTrackUrl, UnsupportedProvider
from app.providers.base import ProviderAvailability
from app.providers.onthespot.provider import OnTheSpotProvider


class FakeBridge:
    def availability(self) -> ProviderAvailability:
        return ProviderAvailability(True, version="test")

    def resolve(self, url: str) -> tuple[str, str, str, Mapping[str, Any]]:
        return (
            "spotify",
            "track",
            "0123456789012345678901",
            {
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
        )


def test_detects_supported_track_url() -> None:
    provider = OnTheSpotProvider(FakeBridge())
    reference = provider.detect_url("https://open.spotify.com/track/0123456789012345678901")
    assert reference.provider is MusicProviderName.SPOTIFY
    assert reference.provider_track_id == "0123456789012345678901"


@pytest.mark.parametrize(
    "url",
    [
        "https://open.spotify.com/album/0123456789012345678901",
        "https://www.deezer.com/album/123",
        "javascript:alert(1)",
    ],
)
def test_rejects_non_track_or_malformed_urls(url: str) -> None:
    with pytest.raises(InvalidTrackUrl):
        OnTheSpotProvider(FakeBridge()).detect_url(url)


def test_rejects_unknown_provider() -> None:
    with pytest.raises(UnsupportedProvider):
        OnTheSpotProvider(FakeBridge()).detect_url("https://example.com/track/123")


def test_rejects_sensitive_query_parameters_before_upstream_logging() -> None:
    with pytest.raises(InvalidTrackUrl):
        OnTheSpotProvider(FakeBridge()).detect_url(
            "https://open.spotify.com/track/0123456789012345678901?access_token=secret"
        )


@pytest.mark.asyncio
async def test_maps_metadata_without_guessing_native_media() -> None:
    provider = OnTheSpotProvider(FakeBridge())
    metadata = await provider.get_metadata("https://open.spotify.com/track/0123456789012345678901")
    assert metadata.title == "Abracadabra"
    assert metadata.duration_ms == 223000
    assert metadata.native.codec is None
    assert metadata.native.container is None
    assert metadata.native.bitrate_kbps is None
    assert "access_token" not in metadata.provider_metadata
