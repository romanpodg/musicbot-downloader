"""Stage 30.6.2 collection routing and completeness regressions."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.download_preferences import UserDownloadPreferences
from app.core.enums import BatchSourceType, MusicProviderName
from app.core.exceptions import UnsupportedAlbum, UnsupportedMediaType
from app.providers.base import AlbumReference, PlaylistReference, TrackReference
from app.providers.onthespot.provider import OnTheSpotProvider
from app.providers.onthespot.worker import (
    MAX_ALBUM_TRACKS,
    OnTheSpotWorker,
    WorkerError,
    _apple_music_song_ids_complete,
    _bandcamp_search_text,
    _qobuz_album_track_ids_complete,
    _youtube_music_playlist_snapshot,
)
from app.services.telegram_media_requests import TelegramMediaRequestService


def _apple_page(*item_ids: str, next_page: str | None = None) -> dict[str, object]:
    result: dict[str, object] = {"data": [{"type": "songs", "id": item_id} for item_id in item_ids]}
    if next_page is not None:
        result["next"] = next_page
    return result


def test_apple_continuation_keeps_order_and_duplicate_occurrences() -> None:
    first = _apple_page(
        *(f"song-{index}" for index in range(1, 101)),
        next_page="/v1/catalog/us/playlists/pl-1/tracks?offset=100",
    )
    continuation = "https://amp-api.music.apple.com/v1/catalog/us/playlists/pl-1/tracks?offset=100"
    calls: list[str] = []

    def fetch(url: str) -> dict[str, object]:
        calls.append(url)
        assert url == continuation
        return _apple_page("song-101", "song-1")

    ids = _apple_music_song_ids_complete(
        first, fetch, base_url="https://amp-api.music.apple.com/v1"
    )

    assert ids == [*(f"song-{index}" for index in range(1, 102)), "song-1"]
    assert calls == [continuation]


def test_apple_continuation_rejects_repeated_or_untrusted_pages() -> None:
    repeated = _apple_page("song-1", next_page="/v1/catalog/us/playlists/pl-1/tracks?offset=100")

    with pytest.raises(WorkerError, match="metadata_unavailable"):
        _apple_music_song_ids_complete(
            repeated,
            lambda _: repeated,
            base_url="https://amp-api.music.apple.com/v1",
        )
    with pytest.raises(WorkerError, match="metadata_unavailable"):
        _apple_music_song_ids_complete(
            _apple_page("song-1", next_page="https://example.invalid/page"),
            lambda _: _apple_page("song-2"),
            base_url="https://amp-api.music.apple.com/v1",
        )


@pytest.mark.parametrize("total", [MAX_ALBUM_TRACKS, str(MAX_ALBUM_TRACKS)])
def test_qobuz_album_boundary_requires_the_reported_total(
    monkeypatch: pytest.MonkeyPatch, total: int | str
) -> None:
    payload = {
        "tracks": {
            "total": total,
            "items": [{"id": index} for index in range(1, MAX_ALBUM_TRACKS + 1)],
        }
    }
    fake_qobuz = SimpleNamespace(
        BASE_URL="https://qobuz.invalid", make_call=lambda *_, **__: payload
    )
    monkeypatch.setattr(
        "app.providers.onthespot.worker.importlib.import_module", lambda _: fake_qobuz
    )

    assert _qobuz_album_track_ids_complete(
        {"user_auth_token": "child-secret", "app_id": "child-app"}, "album-1"
    ) == [str(index) for index in range(1, MAX_ALBUM_TRACKS + 1)]


@pytest.mark.parametrize(
    "payload, code",
    [
        (
            {"tracks": {"total": 501, "items": [{"id": index} for index in range(500)]}},
            "album_too_large",
        ),
        ({"tracks": {"items": [{"id": "track-1"}]}}, "metadata_unavailable"),
        ({"tracks": {"total": 2, "items": [{"id": "track-1"}]}}, "metadata_unavailable"),
    ],
)
def test_qobuz_album_never_accepts_a_truncated_or_unverifiable_page(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, object], code: str
) -> None:
    fake_qobuz = SimpleNamespace(
        BASE_URL="https://qobuz.invalid", make_call=lambda *_, **__: payload
    )
    monkeypatch.setattr(
        "app.providers.onthespot.worker.importlib.import_module", lambda _: fake_qobuz
    )

    with pytest.raises(WorkerError, match=code):
        _qobuz_album_track_ids_complete(
            {"user_auth_token": "child-secret", "app_id": "child-app"}, "album-1"
        )


def test_apple_and_qobuz_collection_urls_are_distinct_from_track_urls() -> None:
    provider = OnTheSpotProvider.__new__(OnTheSpotProvider)
    assert isinstance(
        provider.detect_media("https://music.apple.com/us/album/release/album-1?i=456"),
        TrackReference,
    )
    assert isinstance(
        provider.detect_media("https://music.apple.com/us/album/release/album-1"), AlbumReference
    )
    assert isinstance(
        provider.detect_media("https://music.apple.com/us/playlist/list-1"), PlaylistReference
    )
    assert isinstance(provider.detect_media("https://play.qobuz.com/track/track-1"), TrackReference)
    assert isinstance(provider.detect_media("https://play.qobuz.com/album/album-1"), AlbumReference)
    assert isinstance(
        provider.detect_media("https://play.qobuz.com/playlist/list-1"), PlaylistReference
    )


def _bandcamp_album_worker(track_ids: list[str], total_tracks: int | None) -> OnTheSpotWorker:
    worker = OnTheSpotWorker()
    worker._initialized = True
    worker._accounts = SimpleNamespace(get_account_token=lambda _: None)
    worker._registry = SimpleNamespace(
        SERVICE_ALBUM_TRACK_ID_FUNCTIONS={"bandcamp": lambda _token, _album: track_ids}
    )

    def metadata(_provider: str, track_id: str) -> dict[str, object]:
        result: dict[str, object] = {
            "title": f"Title {track_id.rsplit('/', 1)[-1]}",
            "artists": "Artist",
            "album_name": "Complete album",
            "album_artists": "Artist",
            "track_number": 1,
        }
        if total_tracks is not None:
            result["total_tracks"] = total_tracks
        return result

    worker._raw_track_metadata = metadata  # type: ignore[method-assign]
    return worker


def test_bandcamp_album_snapshot_keeps_jsonld_order_and_duplicate_occurrences() -> None:
    urls = [
        "https://artist.bandcamp.com/track/a?tracking=1",
        "https://artist.bandcamp.com/track/b",
        "https://artist.bandcamp.com/track/a",
        "https://artist.bandcamp.com/track/c",
    ]

    snapshot = _bandcamp_album_worker(urls, 4).resolve_album_id(
        "bandcamp", "https://artist.bandcamp.com/album/release"
    )

    assert [item["provider_track_id"] for item in snapshot["tracks"]] == [
        "https://artist.bandcamp.com/track/a",
        "https://artist.bandcamp.com/track/b",
        "https://artist.bandcamp.com/track/a",
        "https://artist.bandcamp.com/track/c",
    ]
    assert [item["position"] for item in snapshot["tracks"]] == [1, 2, 3, 4]


@pytest.mark.parametrize(
    ("track_ids", "total_tracks"),
    [
        (["https://artist.bandcamp.com/track/a"], 2),
        (["https://invalid.example/track/a"], 1),
    ],
)
def test_bandcamp_album_snapshot_fails_closed_when_incomplete_or_unrepresentable(
    track_ids: list[str], total_tracks: int
) -> None:
    with pytest.raises(WorkerError, match="metadata_unavailable"):
        _bandcamp_album_worker(track_ids, total_tracks).resolve_album_id(
            "bandcamp", "https://artist.bandcamp.com/album/release"
        )


def test_bandcamp_search_display_text_never_leaks_html_fragments() -> None:
    assert _bandcamp_search_text("  <b>Artist &amp; Title</b>  ") == "Artist & Title"


def test_ytm_static_playlist_routing_preserves_track_routing_and_rejects_dynamic_forms() -> None:
    provider = OnTheSpotProvider.__new__(OnTheSpotProvider)
    playlist = provider.detect_media(
        "https://music.youtube.com/playlist?list=PLabc_1234567890&feature=share"
    )
    assert isinstance(playlist, PlaylistReference)
    assert playlist.provider is MusicProviderName.YOUTUBE_MUSIC
    assert playlist.provider_playlist_id == "PLabc_1234567890"
    assert playlist.source_url == "https://music.youtube.com/playlist?list=PLabc_1234567890"

    track = provider.detect_media(
        "https://music.youtube.com/watch?v=abc_123-XYZ&list=PLabc_1234567890"
    )
    assert isinstance(track, TrackReference)
    assert track.source_url == "https://music.youtube.com/watch?v=abc_123-XYZ"

    for collection_id in ("RDCLAK5uy_dynamic", "OLAK5uy_album_form", "LLliked_library"):
        with pytest.raises(UnsupportedMediaType):
            provider.detect_media(f"https://music.youtube.com/playlist?list={collection_id}")


def test_ytm_flat_snapshot_requires_declared_complete_count_and_preserves_occurrences() -> None:
    payload = {
        "id": "PLabc_1234567890",
        "title": "Sparse YTM playlist",
        "channel": "Playlist channel",
        "playlist_count": 4,
        "entries": (
            {"id": "video-one", "title": "First", "channel": "Artist one", "duration": 180},
            {"id": "video-two", "title": "Second"},
            {"id": "video-one", "title": "First again", "duration": 181},
            {"id": "private-video", "title": "[Private video]", "availability": "private"},
        ),
    }
    title, creator, items = _youtube_music_playlist_snapshot(payload, "PLabc_1234567890")

    assert (title, creator) == ("Sparse YTM playlist", "Playlist channel")
    assert [(item["position"], item["provider_media_id"]) for item in items] == [
        (1, "video-one"),
        (2, "video-two"),
        (3, "video-one"),
        (4, "private-video"),
    ]
    assert items[0]["duration_ms"] == 180_000
    assert items[1]["artist"] is None and items[1]["duration_ms"] is None


def test_ytm_snapshot_materializes_all_flattened_continuation_entries_in_order() -> None:
    playlist_id = "PLabc_1234567890"

    def entries():
        yield from ({"id": "first"}, {"id": "boundary"})
        # This second fixture segment models the extractor-owned continuation.
        yield from ({"id": "after-boundary"}, {"id": "first"})

    _, _, items = _youtube_music_playlist_snapshot(
        {"id": playlist_id, "playlist_count": 4, "entries": entries()}, playlist_id
    )
    assert [item["provider_media_id"] for item in items] == [
        "first",
        "boundary",
        "after-boundary",
        "first",
    ]


async def test_ytm_complete_snapshot_is_normalized_through_the_existing_playlist_adapter() -> None:
    playlist_id = "PLabc_1234567890"
    process = SimpleNamespace(
        resolve_playlist=AsyncMock(
            return_value={
                "provider": "youtube_music",
                "provider_playlist_id": playlist_id,
                "title": "YTM discovery",
                "creator": "Channel",
                "items": [
                    {
                        "position": 1,
                        "provider_media_id": "video-id",
                        "title": "Sparse title",
                        "source_url": "https://music.youtube.com/watch?v=video-id",
                    }
                ],
            }
        )
    )
    provider = OnTheSpotProvider(process)  # type: ignore[arg-type]

    collection = await provider.get_playlist(
        f"https://music.youtube.com/playlist?list={playlist_id}"
    )

    assert collection.provider is MusicProviderName.YOUTUBE_MUSIC
    assert collection.collection_id == playlist_id
    assert [(item.position, item.provider_media_id) for item in collection.items] == [
        (1, "video-id")
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "PLabc_1234567890", "playlist_count": 3, "entries": [{"id": "only-one"}]},
        {"id": "PLabc_1234567890", "entries": [{"id": "only-one"}]},
        {"id": "PLabc_1234567890", "playlist_count": 1, "entries": [{"title": "removed"}]},
    ],
)
def test_ytm_snapshot_fails_closed_for_partial_or_unrepresentable_entries(
    payload: dict[str, object],
) -> None:
    with pytest.raises(WorkerError, match="metadata_unavailable"):
        _youtube_music_playlist_snapshot(payload, "PLabc_1234567890")


async def test_qobuz_playlist_is_explicitly_deferred_before_ipc() -> None:
    provider = OnTheSpotProvider.__new__(OnTheSpotProvider)
    provider._process_client = SimpleNamespace(
        resolve_playlist=lambda _: pytest.fail("unexpected IPC")
    )

    with pytest.raises(UnsupportedAlbum):
        await provider.get_playlist("https://play.qobuz.com/playlist/list-1")


async def test_ytm_playlist_enters_the_existing_stage23_batch_service() -> None:
    batches = _Batches()
    service = TelegramMediaRequestService(
        _Provider(
            PlaylistReference(
                MusicProviderName.YOUTUBE_MUSIC,
                "PLabc_1234567890",
                "https://music.youtube.com/playlist?list=PLabc_1234567890",
            )
        ),  # type: ignore[arg-type]
        _Tracks(),  # type: ignore[arg-type]
        _Albums(),  # type: ignore[arg-type]
        batches,  # type: ignore[arg-type]
        _Preferences(),  # type: ignore[arg-type]
    )

    admission = await service.request(
        user=SimpleNamespace(id=71),
        telegram_chat_id=72,
        source_message_id=73,
        url="https://music.youtube.com/playlist?list=PLabc_1234567890",
    )

    assert admission.batch is not None
    assert batches.calls[0]["source_type"] is BatchSourceType.PLAYLIST
    assert (
        batches.calls[0]["source_reference"]
        == "https://music.youtube.com/playlist?list=PLabc_1234567890"
    )


@dataclass
class _Provider:
    reference: TrackReference | AlbumReference | PlaylistReference

    async def classify_url(self, _: str) -> TrackReference | AlbumReference | PlaylistReference:
        return self.reference


@dataclass
class _Tracks:
    calls: list[str] = field(default_factory=list)

    async def request_track(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs["url"])
        return SimpleNamespace(id=1)


@dataclass
class _Albums:
    calls: int = 0

    async def request_album(self, **_: Any) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(id=2)


@dataclass
class _Batches:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def expand(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(id=3)


class _Preferences:
    async def get_for_user(self, user_id: int) -> UserDownloadPreferences:
        return UserDownloadPreferences(user_id)


@pytest.mark.parametrize(
    "provider",
    [MusicProviderName.APPLE_MUSIC, MusicProviderName.BANDCAMP, MusicProviderName.QOBUZ],
)
async def test_supported_provider_albums_enter_the_existing_batch_service(
    provider: MusicProviderName,
) -> None:
    batches = _Batches()
    albums = _Albums()
    service = TelegramMediaRequestService(
        _Provider(AlbumReference(provider, "album-1", f"https://example.test/{provider.value}")),  # type: ignore[arg-type]
        _Tracks(),  # type: ignore[arg-type]
        albums,  # type: ignore[arg-type]
        batches,  # type: ignore[arg-type]
        _Preferences(),  # type: ignore[arg-type]
    )

    admission = await service.request(
        user=SimpleNamespace(id=71), telegram_chat_id=72, source_message_id=73, url="ignored"
    )

    assert admission.batch is not None and admission.album is None
    assert albums.calls == 0
    assert batches.calls[0]["source_type"] is BatchSourceType.ALBUM
    assert batches.calls[0]["source_reference"] == f"https://example.test/{provider.value}"
