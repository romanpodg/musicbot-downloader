"""Stage 30.6.2 collection routing and completeness regressions."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.download_preferences import UserDownloadPreferences
from app.core.enums import BatchSourceType, MusicProviderName
from app.core.exceptions import UnsupportedAlbum
from app.providers.base import AlbumReference, PlaylistReference, TrackReference
from app.providers.onthespot.provider import OnTheSpotProvider
from app.providers.onthespot.worker import (
    MAX_ALBUM_TRACKS,
    WorkerError,
    _apple_music_song_ids_complete,
    _qobuz_album_track_ids_complete,
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


async def test_qobuz_playlist_is_explicitly_deferred_before_ipc() -> None:
    provider = OnTheSpotProvider.__new__(OnTheSpotProvider)
    provider._process_client = SimpleNamespace(
        resolve_playlist=lambda _: pytest.fail("unexpected IPC")
    )

    with pytest.raises(UnsupportedAlbum):
        await provider.get_playlist("https://play.qobuz.com/playlist/list-1")


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


@pytest.mark.parametrize("provider", [MusicProviderName.APPLE_MUSIC, MusicProviderName.QOBUZ])
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
