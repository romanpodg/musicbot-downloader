from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.core.enums import AlbumRequestStatus, MusicProviderName, QualityProfile
from app.core.exceptions import AlbumTooLarge, UnsupportedMediaType
from app.i18n import LocalizationService
from app.providers.base import AlbumReference, PlaylistReference, TrackReference
from app.providers.onthespot.provider import OnTheSpotProvider
from app.services.telegram_albums import AlbumCard, AlbumSelectionPage
from app.storage.models import TelegramAlbumItem
from app.telegram.presentation import (
    TelegramPresentation,
    encode_album_clear_all,
    encode_album_download_all,
    encode_album_download_selected,
    encode_album_first_quality,
    encode_album_other_quality,
    encode_album_page,
    encode_album_quality,
    encode_album_quality_back,
    encode_album_select_all,
    encode_album_select_tracks,
    encode_album_selection_back,
    encode_album_toggle,
    parse_album_first_quality,
    parse_album_page,
    parse_album_quality,
    parse_album_toggle,
)


@dataclass
class Process:
    album: dict[str, Any]
    soundcloud_type: str = "album"

    async def resolve_album(self, url: str) -> dict[str, Any]:
        return self.album

    async def match_url(self, url: str) -> dict[str, Any]:
        return {
            "service": "soundcloud",
            "item_type": self.soundcloud_type,
            "item_id": "77",
        }


@pytest.mark.parametrize(
    ("url", "provider", "album_id"),
    [
        (
            "https://music.apple.com/us/album/synthetic-release/123",
            MusicProviderName.APPLE_MUSIC,
            "123",
        ),
        (
            "https://artist.bandcamp.com/album/synthetic-release",
            MusicProviderName.BANDCAMP,
            "https://artist.bandcamp.com/album/synthetic-release",
        ),
        ("https://www.deezer.com/album/123", MusicProviderName.DEEZER, "123"),
        ("https://play.qobuz.com/album/abc", MusicProviderName.QOBUZ, "abc"),
        (
            "https://open.spotify.com/album/0123456789012345678901",
            MusicProviderName.SPOTIFY,
            "0123456789012345678901",
        ),
        ("https://tidal.com/browse/album/123", MusicProviderName.TIDAL, "123"),
    ],
)
def test_album_urls_are_recognized_separately_from_tracks(
    url: str, provider: MusicProviderName, album_id: str
) -> None:
    reference = OnTheSpotProvider.__new__(OnTheSpotProvider).detect_media(url)
    assert reference == AlbumReference(provider, album_id, reference.source_url)


def test_track_playlist_and_malformed_routing_remain_distinct() -> None:
    provider = OnTheSpotProvider.__new__(OnTheSpotProvider)
    assert isinstance(
        provider.detect_media("https://open.spotify.com/track/0123456789012345678901"),
        TrackReference,
    )
    assert isinstance(
        provider.detect_media("https://open.spotify.com/playlist/0123456789012345678901"),
        PlaylistReference,
    )
    with pytest.raises(UnsupportedMediaType):
        provider.detect_media("https://music.youtube.com/playlist?list=synthetic")


async def test_soundcloud_album_type_uses_isolated_match_boundary() -> None:
    process = Process({})
    provider = OnTheSpotProvider(process)  # type: ignore[arg-type]
    reference = await provider.classify_url("https://soundcloud.com/artist/release")
    assert reference == AlbumReference(
        MusicProviderName.SOUNDCLOUD,
        "77",
        "https://soundcloud.com/artist/release",
    )


async def test_normalized_album_snapshot_preserves_order_multidisc_and_unicode() -> None:
    raw = {
        "provider": "spotify",
        "provider_album_id": "0123456789012345678901",
        "title": "Длинный релиз 日本語 🎵",
        "artist": "Album Artist",
        "release_date": "2026",
        "duration_ms": None,
        "tracks": [
            {
                "provider_track_id": f"track-{position}",
                "position": position,
                "disc_number": 1 if position < 3 else 2,
                "track_number": position if position < 3 else position - 2,
                "title": f"Track {position}",
                "artist": "Track Artist" if position == 2 else "Album Artist",
                "duration_ms": None if position == 3 else 180_000,
                "explicit": position == 2,
            }
            for position in range(1, 5)
        ],
    }
    provider = OnTheSpotProvider(Process(raw))  # type: ignore[arg-type]
    snapshot = await provider.get_album("https://open.spotify.com/album/0123456789012345678901")
    assert snapshot.title == "Длинный релиз 日本語 🎵"
    assert snapshot.duration_ms is None
    assert [item.position for item in snapshot.tracks] == [1, 2, 3, 4]
    assert snapshot.tracks[1].artist == "Track Artist"
    assert snapshot.tracks[2].disc_number == 2
    assert snapshot.tracks[2].track_number == 1


async def test_album_snapshot_limit_is_rejected_not_truncated() -> None:
    raw = {
        "provider": "spotify",
        "provider_album_id": "0123456789012345678901",
        "title": "Too Large",
        "artist": "Artist",
        "tracks": [{"provider_track_id": str(index), "position": index} for index in range(1, 502)],
    }
    provider = OnTheSpotProvider(Process(raw))  # type: ignore[arg-type]
    with pytest.raises(AlbumTooLarge):
        await provider.get_album("https://open.spotify.com/album/0123456789012345678901")


def test_album_callbacks_are_compact_versioned_and_round_trip() -> None:
    request_id = 987654321
    item_id = 123456789
    values = [
        encode_album_download_all(request_id),
        encode_album_select_tracks(request_id),
        encode_album_other_quality(request_id),
        encode_album_toggle(request_id, item_id, 31),
        encode_album_page(request_id, 31),
        encode_album_select_all(request_id),
        encode_album_clear_all(request_id),
        encode_album_download_selected(request_id),
        encode_album_selection_back(request_id),
        encode_album_quality_back(request_id),
    ]
    for quality in QualityProfile:
        values.extend(
            [
                encode_album_first_quality(request_id, quality),
                encode_album_quality(request_id, quality),
            ]
        )
    assert all(len(value.encode()) < 64 for value in values)
    toggle = parse_album_toggle(encode_album_toggle(request_id, item_id, 31))
    page = parse_album_page(encode_album_page(request_id, 31))
    first = parse_album_first_quality(
        encode_album_first_quality(request_id, QualityProfile.AAC_256)
    )
    one_off = parse_album_quality(encode_album_quality(request_id, QualityProfile.LOSSLESS))
    assert toggle and (toggle.request_id, toggle.item_id, toggle.page) == (
        request_id,
        item_id,
        31,
    )
    assert page and (page.request_id, page.page) == (request_id, 31)
    assert first and first.quality_profile is QualityProfile.AAC_256
    assert one_off and one_off.quality_profile is QualityProfile.LOSSLESS


def test_multidisc_selection_presentation_is_bounded_and_paginated() -> None:
    presentation = TelegramPresentation(LocalizationService(("en", "ru"), "en"))
    card = AlbumCard(
        7,
        AlbumRequestStatus.SELECTING_TRACKS,
        QualityProfile.MP3_320,
        "Artist",
        "Album",
        "2026",
        3_600_000,
        25,
        99,
    )
    items = tuple(
        TelegramAlbumItem(
            id=index,
            album_request_id=7,
            position=index,
            disc_number=2,
            track_number=index - 8,
            provider_track_id=f"id-{index}",
            title="日本語 🎵 " + "x" * 100,
            selected=index % 2 == 0,
        )
        for index in range(9, 17)
    )
    page = AlbumSelectionPage(card, items, 1, 4, 4)
    keyboard = presentation.album_selection_keyboard(page, "en")
    assert len(keyboard.inline_keyboard) == 12
    assert all(len(row[0].text) <= 48 for row in keyboard.inline_keyboard[:8])
    assert keyboard.inline_keyboard[0][0].text.startswith("☐ D2 · 01.")
    assert [button.text for button in keyboard.inline_keyboard[8]] == ["Previous", "Next"]
