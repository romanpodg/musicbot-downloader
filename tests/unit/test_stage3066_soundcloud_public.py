"""Stage 30.6.6 public-only SoundCloud worker regressions."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pytest

from app.providers.onthespot.worker import (
    OnTheSpotWorker,
    _public_soundcloud_media,
)


class _Config:
    def __init__(self, accounts: list[dict[str, Any]]) -> None:
        self.values: dict[str, Any] = {"accounts": accounts, "active_account_number": 0}

    def get(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)

    def set(self, key: str, value: object) -> None:
        self.values[key] = value


class _Accounts:
    def __init__(self, config: _Config) -> None:
        self._config = config

    def get_account_token(self, provider: str) -> object:
        assert provider == "soundcloud"
        index = self._config.get("active_account_number")
        assert isinstance(index, int)
        accounts = self._config.get("accounts")
        assert isinstance(accounts, list)
        return accounts[index]["token"]


class _Registry:
    def __init__(self) -> None:
        self.tokens: list[object] = []

    def get_metadata_function(self, provider: str, item_type: str):  # type: ignore[no-untyped-def]
        assert (provider, item_type) == ("soundcloud", "track")

        def metadata(token: object, provider_track_id: str) -> dict[str, object]:
            self.tokens.append(token)
            assert provider_track_id == "https://soundcloud.com/artist/track"
            return {"is_playable": True}

        return metadata


def _worker_with_public_and_oauth() -> tuple[OnTheSpotWorker, _Registry, dict[str, str]]:
    public_token = {"client": "child-owned-public-state"}
    oauth_token = {"oauth_token": "must-not-be-selected"}
    public = {
        "uuid": "public",
        "service": "soundcloud",
        "status": "active",
        "account_type": "public",
        "bitrate": "128k",
        "login": public_token,
        "token": public_token,
    }
    oauth = {
        "uuid": "oauth",
        "service": "soundcloud",
        "status": "active",
        "account_type": "premium",
        "bitrate": "256k",
        "login": oauth_token,
        "token": oauth_token,
    }
    config = _Config([oauth, public])
    registry = _Registry()
    worker = OnTheSpotWorker()
    worker._initialized = True
    worker._config = config
    worker._accounts = _Accounts(config)
    worker._registry = registry
    # Put OAuth first in the runtime pool to prove account order is irrelevant.
    worker._runtime = SimpleNamespace(account_pool=[oauth, public])
    return worker, registry, public_token


def test_public_soundcloud_source_check_ignores_coexisting_oauth_state() -> None:
    worker, registry, public_token = _worker_with_public_and_oauth()

    result = worker.check_source("soundcloud", "https://soundcloud.com/artist/track")

    assert result == {"status": "AVAILABLE"}
    assert registry.tokens == [public_token]
    assert worker.list_provider_accounts("soundcloud") == []


def test_public_soundcloud_preflight_uses_only_pinned_selector_and_sanitizes_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class _YoutubeDL:
        def __init__(self, options: dict[str, object]) -> None:
            observed["options"] = options

        def __enter__(self) -> _YoutubeDL:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def extract_info(self, url: str, *, download: bool) -> dict[str, object]:
            observed["url"] = url
            observed["download"] = download
            return {
                "requested_downloads": [
                    {
                        "format_id": "http_mp3_0_0",
                        "ext": "mp3",
                        "acodec": "mp3",
                        "abr": 128,
                        "protocol": "http",
                        "url": "https://signed.example.test/must-not-escape",
                    }
                ]
            }

    real_import = importlib.import_module

    def fake_import(name: str) -> object:
        if name == "yt_dlp":
            return SimpleNamespace(YoutubeDL=_YoutubeDL)
        return real_import(name)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    worker, _, public_token = _worker_with_public_and_oauth()

    with worker._public_soundcloud_execution() as token:
        result = worker._prepare_soundcloud(token, "https://soundcloud.com/artist/track")

    assert token is public_token
    assert observed["options"] == {
        "format": "bestaudio[ext=mp3]",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    assert observed["url"] == "https://soundcloud.com/artist/track"
    assert observed["download"] is False
    assert result == {"codec": "mp3", "container": "mp3", "bitrate_kbps": 128, "lossless": False}


@pytest.mark.parametrize(
    "selected",
    [
        {
            "format_id": "http_mp3_0_0",
            "ext": "m4a",
            "acodec": "aac",
            "abr": 128,
            "protocol": "http",
        },
        {
            "format_id": "http_mp3_0_0",
            "ext": "mp3",
            "acodec": "mp3",
            "abr": 320,
            "protocol": "http",
        },
        {
            "format_id": "http_mp3_0_0",
            "ext": "mp3",
            "acodec": "mp3",
            "abr": None,
            "protocol": "http",
        },
        {
            "format_id": "hls_mp3_0_0",
            "ext": "mp3",
            "acodec": "mp3",
            "abr": 128,
            "protocol": "m3u8_native",
        },
    ],
)
def test_public_soundcloud_preflight_rejects_ambiguous_or_unexpected_media(
    selected: dict[str, object],
) -> None:
    assert _public_soundcloud_media({"requested_downloads": [selected]}) is None
