from __future__ import annotations

import errno
import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.providers.onthespot.worker import OnTheSpotWorker, WorkerError


class FakeAccounts:
    def __init__(self, token: Any = None) -> None:
        self.token = token

    def get_account_token(self, provider: str) -> Any:
        return self.token


class FakeRegistry:
    def __init__(self, result: object) -> None:
        self.result = result

    def get_metadata_function(self, provider: str, item_type: str) -> Any:
        def metadata(token: Any, provider_track_id: str) -> object:
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

        return metadata


def _worker(
    provider: str,
    result: object,
    *,
    token: Any = None,
    active: bool = True,
    account_type: str = "public",
    bitrate: str = "128k",
) -> OnTheSpotWorker:
    worker = OnTheSpotWorker()
    worker._initialized = True
    worker._accounts = FakeAccounts(token)
    worker._registry = FakeRegistry(result)
    account = {
        "uuid": "test",
        "service": provider,
        "status": "active",
        "account_type": account_type,
        "bitrate": bitrate,
        "login": token,
    }
    worker._runtime = SimpleNamespace(account_pool=[account] if active else [])
    return worker


def test_available_source_returns_only_normalized_facts() -> None:
    result = _worker("bandcamp", {"is_playable": True}).check_source("bandcamp", "track-id")
    assert result == {
        "status": "AVAILABLE",
        "native": {"codec": "mp3", "container": "mp3", "bitrate_kbps": 128},
    }


def test_authenticated_provider_without_active_account_requires_auth() -> None:
    result = _worker("spotify", {}, active=False).check_source("spotify", "track-id")
    assert result == {
        "status": "AUTH_REQUIRED",
        "error_code": "authentication_required",
    }


def test_free_apple_account_is_not_download_ready() -> None:
    result = _worker(
        "apple_music",
        {"is_playable": True},
        token=object(),
        account_type="free",
        bitrate="256k",
    ).check_source("apple_music", "track-id")
    assert result == {
        "status": "AUTH_REQUIRED",
        "error_code": "authentication_required",
    }


def test_public_provider_without_active_runtime_is_unavailable() -> None:
    result = _worker("bandcamp", {}, active=False).check_source("bandcamp", "track-id")
    assert result == {"status": "UNAVAILABLE", "error_code": "provider_unavailable"}


def test_false_playability_is_source_unavailable() -> None:
    result = _worker("youtube_music", {"is_playable": False}).check_source(
        "youtube_music", "track-id"
    )
    assert result == {
        "status": "SOURCE_UNAVAILABLE",
        "error_code": "source_unavailable",
    }


def test_unsupported_provider_has_explicit_status() -> None:
    result = _worker("unknown", {}).check_source("unknown", "track-id")
    assert result == {"status": "UNSUPPORTED", "error_code": "provider_not_downloadable"}


def test_unexpected_provider_exception_is_normalized() -> None:
    result = _worker("bandcamp", RuntimeError("raw upstream detail")).check_source(
        "bandcamp", "track-id"
    )
    assert result == {"status": "ERROR", "error_code": "provider_error"}


@pytest.mark.parametrize("status_code", [401, 403])
def test_http_auth_failure_is_normalized(status_code: int) -> None:
    error = RuntimeError("secret upstream detail")
    error.response = SimpleNamespace(status_code=status_code)  # type: ignore[attr-defined]
    result = _worker("spotify", error, token=object(), account_type="premium").check_source(
        "spotify", "track-id"
    )
    assert result == {
        "status": "AUTH_REQUIRED",
        "error_code": "authentication_required",
    }


def test_native_download_bypasses_upstream_conversion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = _worker("bandcamp", {"is_playable": True}, token=object(), account_type="public")
    job_id = "a" * 32
    source = tmp_path / job_id / "attempt-001" / "source"
    source.mkdir(parents=True)
    monkeypatch.setenv("MUSICBOT_TEMP_DIR", str(tmp_path))

    class FakeDownloadWorker:
        def __init__(self, gui: bool) -> None:
            assert gui is False

        def _download(self, *args: Any) -> tuple[str, str, list[object]]:
            Path(args[6]).write_bytes(b"provider native audio")
            return ".mp3", "128k", []

    real_import = importlib.import_module

    def fake_import(name: str) -> Any:
        if name == "onthespot.downloader":
            return SimpleNamespace(DownloadWorker=FakeDownloadWorker)
        if name == "onthespot.constants":
            return SimpleNamespace(ItemStatus=SimpleNamespace(DOWNLOADING="downloading"))
        return real_import(name)

    monkeypatch.setattr(importlib, "import_module", fake_import)

    result = worker.download_native("bandcamp", "track-id", job_id, 1)

    assert result["native_encoded"] is True
    assert result["upstream_quality_transcoded"] is False
    assert result["provider_decrypted"] is False
    assert Path(result["file_path"]).name == "native.mp3"
    assert Path(result["file_path"]).read_bytes() == b"provider native audio"


def test_native_download_normalizes_enospc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worker = _worker("bandcamp", {"is_playable": True}, token=object(), account_type="public")
    job_id = "b" * 32
    (tmp_path / job_id / "attempt-001" / "source").mkdir(parents=True)
    monkeypatch.setenv("MUSICBOT_TEMP_DIR", str(tmp_path))

    class FullDiskDownloadWorker:
        def __init__(self, gui: bool) -> None:
            assert gui is False

        def _download(self, *args: object) -> tuple[str, str, list[object]]:
            raise OSError(errno.ENOSPC, "controlled")

    real_import = importlib.import_module

    def fake_import(name: str) -> object:
        if name == "onthespot.downloader":
            return SimpleNamespace(DownloadWorker=FullDiskDownloadWorker)
        if name == "onthespot.constants":
            return SimpleNamespace(ItemStatus=SimpleNamespace(DOWNLOADING="downloading"))
        return real_import(name)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    with pytest.raises(WorkerError) as raised:
        worker.download_native("bandcamp", "track-id", job_id, 1)
    assert raised.value.code == "temporary_storage_unavailable"


def test_worker_rejects_download_path_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = _worker("bandcamp", {"is_playable": True})
    monkeypatch.setenv("MUSICBOT_TEMP_DIR", str(tmp_path))

    with pytest.raises(WorkerError):
        worker._download_destination("../outside", 1)
