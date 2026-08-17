"""Isolated OnTheSpot JSON Lines worker.

This module intentionally sets compatibility environment variables before any
third-party provider import. Protocol output is the original stdout stream;
upstream stdout and stderr are discarded so they cannot corrupt framing or
reach parent application logs.
"""

from __future__ import annotations

import os

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
os.environ["LOG_LEVEL"] = "20"

import importlib
import importlib.metadata
import json
import re
import sys
from collections.abc import Mapping
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, TextIO

from app.providers.onthespot.ipc import (
    CHECK_SOURCE_METHOD,
    DOWNLOAD_NATIVE_METHOD,
    GET_METADATA_METHOD,
    INITIALIZE_METHOD,
    LIST_SEARCHABLE_PROVIDERS_METHOD,
    MAX_MESSAGE_BYTES,
    PREPARE_SOURCE_METHOD,
    PROTOCOL_VERSION,
    SEARCH_TRACKS_METHOD,
    SHUTDOWN_METHOD,
)

_AUTHENTICATED_SERVICES = frozenset(
    {"apple_music", "deezer", "qobuz", "soundcloud", "spotify", "tidal"}
)
_USER_AUTH_SERVICES = frozenset({"apple_music", "deezer", "qobuz", "spotify", "tidal"})
_DOWNLOAD_SERVICES = frozenset(
    {
        "apple_music",
        "bandcamp",
        "deezer",
        "qobuz",
        "soundcloud",
        "spotify",
        "tidal",
        "youtube_music",
    }
)
_WIRE_METADATA_KEYS = frozenset(
    {
        "album_name",
        "artists",
        "explicit",
        "is_playable",
        "isrc",
        "item_id",
        "length",
        "release_date",
        "release_year",
        "title",
    }
)
_SEARCHABLE_SERVICES = frozenset(
    {
        "apple_music",
        "bandcamp",
        "deezer",
        "qobuz",
        "soundcloud",
        "spotify",
        "tidal",
        "youtube_music",
    }
)
_MAX_SEARCH_RESULTS = 10
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")


class WorkerError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class OnTheSpotWorker:
    def __init__(self) -> None:
        self._initialized = False
        self._accounts: Any = None
        self._parse: Any = None
        self._registry: Any = None
        self._config: Any = None
        self._runtime: Any = None

    def initialize(self) -> dict[str, Any]:
        if self._initialized:
            return self._initialization_result()
        self._validate_config_location()
        try:
            with _silence_upstream():
                self._accounts = importlib.import_module("onthespot.accounts")
                self._parse = importlib.import_module("onthespot.parse_item")
                self._registry = importlib.import_module("onthespot.api.registry")
                self._config = importlib.import_module("onthespot.otsconfig").config
                self._runtime = importlib.import_module("onthespot.runtimedata")
                self._accounts.AccountPoolLoader(gui=False).run()
            self._initialized = True
        except Exception as exc:
            raise WorkerError("provider_unavailable") from exc
        return self._initialization_result()

    def get_metadata(self, url: str) -> dict[str, Any]:
        if not self._initialized:
            self.initialize()
        try:
            with _silence_upstream():
                resolved = self._parse.UrlMatcher().match(url)
        except Exception as exc:
            raise WorkerError("metadata_unavailable") from exc
        if resolved is None:
            raise WorkerError("invalid_track_url")
        service, item_type, item_id = resolved
        if service == "__handled__" or item_type != "track" or not item_id:
            raise WorkerError("invalid_track_url")

        try:
            with _silence_upstream():
                token = self._accounts.get_account_token(service)
                if service in _AUTHENTICATED_SERVICES and token is None:
                    raise WorkerError("provider_authentication_error")
                metadata_function = self._registry.get_metadata_function(service, item_type)
                raw = metadata_function(token, item_id)
        except WorkerError:
            raise
        except (KeyError, IndexError) as exc:
            raise WorkerError("provider_authentication_error") from exc
        except Exception as exc:
            raise WorkerError("metadata_unavailable") from exc
        if not isinstance(raw, Mapping) or not raw:
            raise WorkerError("metadata_unavailable")

        return {
            "service": str(service),
            "item_type": str(item_type),
            "item_id": str(item_id),
            "metadata": {
                key: _wire_value(raw[key])
                for key in _WIRE_METADATA_KEYS
                if key in raw and _wire_value(raw[key]) is not None
            },
        }

    def list_searchable_providers(self) -> list[str]:
        if not self._initialized:
            self.initialize()
        registered = self._registry.SERVICE_SEARCH_FUNCTIONS
        return sorted(
            {
                str(account.get("service"))
                for account in self._runtime.account_pool
                if isinstance(account, Mapping)
                and account.get("status") == "active"
                and account.get("service") in registered
                and account.get("service") in _SEARCHABLE_SERVICES
            }
        )

    def search_tracks(self, provider: str, query: str, limit: int) -> list[dict[str, str]]:
        if not self._initialized:
            self.initialize()
        if (
            provider not in _SEARCHABLE_SERVICES
            or not query.strip()
            or len(query) > 1024
            or isinstance(limit, bool)
            or not 1 <= limit <= _MAX_SEARCH_RESULTS
        ):
            raise WorkerError("unsupported_provider")
        if provider not in self.list_searchable_providers():
            code = (
                "provider_authentication_error"
                if provider in _AUTHENTICATED_SERVICES
                else "provider_unavailable"
            )
            raise WorkerError(code)
        search_function = self._registry.SERVICE_SEARCH_FUNCTIONS.get(provider)
        if search_function is None:
            raise WorkerError("unsupported_provider")
        try:
            with _silence_upstream():
                token = self._accounts.get_account_token(provider)
                if provider in _AUTHENTICATED_SERVICES and token is None:
                    raise WorkerError("provider_authentication_error")
                previous_limit = self._config.get("max_search_results")
                self._config.set("max_search_results", limit)
                try:
                    raw = search_function(token, query.strip(), ["track"])
                finally:
                    self._config.set("max_search_results", previous_limit)
        except WorkerError:
            raise
        except (KeyError, IndexError) as exc:
            raise WorkerError("provider_authentication_error") from exc
        except Exception as exc:
            raise WorkerError("provider_unavailable") from exc
        if not isinstance(raw, list):
            raise WorkerError("provider_unavailable")

        candidates: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            if item.get("item_service") != provider or item.get("item_type") != "track":
                continue
            item_id = item.get("item_id")
            url = item.get("item_url")
            if item_id is None or not isinstance(url, str) or not url:
                continue
            candidate = {"provider": provider, "provider_track_id": str(item_id), "url": url}
            title = item.get("item_name")
            artist = item.get("item_by")
            if title is not None:
                candidate["title"] = str(title)
            if artist is not None:
                candidate["artist"] = str(artist)
            candidates.append(candidate)
            if len(candidates) == limit:
                break
        return candidates

    def check_source(self, provider: str, provider_track_id: str) -> dict[str, Any]:
        """Return only normalized readiness facts; never return account or token data."""

        if not self._initialized:
            self.initialize()
        if (
            provider not in _DOWNLOAD_SERVICES
            or not provider_track_id
            or len(provider_track_id) > 2048
        ):
            return _source_result("UNSUPPORTED", "provider_not_downloadable")

        active_accounts = [
            account
            for account in self._runtime.account_pool
            if isinstance(account, Mapping)
            and account.get("service") == provider
            and account.get("status") == "active"
        ]
        if not active_accounts:
            status = "AUTH_REQUIRED" if provider in _USER_AUTH_SERVICES else "UNAVAILABLE"
            code = (
                "authentication_required" if status == "AUTH_REQUIRED" else "provider_unavailable"
            )
            return _source_result(status, code)

        try:
            with _silence_upstream():
                token = self._accounts.get_account_token(provider)
                if provider in _AUTHENTICATED_SERVICES and token is None:
                    status = "AUTH_REQUIRED" if provider in _USER_AUTH_SERVICES else "UNAVAILABLE"
                    code = (
                        "authentication_required"
                        if status == "AUTH_REQUIRED"
                        else "provider_unavailable"
                    )
                    return _source_result(status, code)
                metadata_function = self._registry.get_metadata_function(provider, "track")
                raw = metadata_function(token, provider_track_id)
        except (KeyError, IndexError):
            status = "AUTH_REQUIRED" if provider in _USER_AUTH_SERVICES else "ERROR"
            code = "authentication_required" if status == "AUTH_REQUIRED" else "provider_error"
            return _source_result(status, code)
        except Exception as exc:
            return self._source_exception_result(provider, exc)

        if not isinstance(raw, Mapping) or not raw:
            return _source_result("ERROR", "source_check_failed")
        if raw.get("is_playable") is False:
            return _source_result("SOURCE_UNAVAILABLE", "source_unavailable")
        selected_account = _selected_account(active_accounts, token)
        if provider == "apple_music" and selected_account.get("account_type") != "premium":
            return _source_result("AUTH_REQUIRED", "authentication_required")
        return _source_result("AVAILABLE", native=_native_media(provider, selected_account))

    def prepare_source(self, provider: str, provider_track_id: str) -> dict[str, Any]:
        """Inspect selected native media without returning URLs, manifests, or credentials."""

        status = self.check_source(provider, provider_track_id)
        if status.get("status") != "AVAILABLE":
            return status
        native = status.get("native")
        if isinstance(native, dict):
            return {"status": "AVAILABLE", "native": native}
        try:
            with _silence_upstream():
                token = self._accounts.get_account_token(provider)
                if token is None:
                    raise WorkerError("provider_authentication_error")
                if provider == "deezer":
                    native = self._prepare_deezer(token, provider_track_id)
                elif provider == "tidal":
                    native = self._prepare_tidal(token, provider_track_id)
                elif provider == "soundcloud":
                    native = self._prepare_soundcloud(token, provider_track_id)
                else:
                    native = None
        except WorkerError:
            raise
        except Exception:
            return _source_result("ERROR", "preflight_failed")
        return _source_result("AVAILABLE", native=native)

    def download_native(
        self, provider: str, provider_track_id: str, job_id: str, plan_rank: int
    ) -> dict[str, Any]:
        """Run only pinned service download/decryption code; skip all quality post-processing."""

        status = self.check_source(provider, provider_track_id)
        if status.get("status") != "AVAILABLE":
            return status
        partial = self._download_destination(job_id, plan_rank)
        try:
            with _silence_upstream():
                token = self._accounts.get_account_token(provider)
                if token is None and provider in _AUTHENTICATED_SERVICES:
                    raise WorkerError("provider_authentication_error")
                metadata_function = self._registry.get_metadata_function(provider, "track")
                metadata = metadata_function(token, provider_track_id)
                if not isinstance(metadata, Mapping) or metadata.get("is_playable") is False:
                    raise WorkerError("metadata_unavailable")
                downloader_module = importlib.import_module("onthespot.downloader")
                constants = importlib.import_module("onthespot.constants")
                item = {
                    "local_id": f"stage6-{job_id}-{plan_rank}",
                    "item_service": provider,
                    "item_type": "track",
                    "item_id": provider_track_id,
                    "item_status": constants.ItemStatus.DOWNLOADING,
                }
                worker = downloader_module.DownloadWorker(gui=False)
                default_format, bitrate, _ = worker._download(
                    item,
                    metadata,
                    provider,
                    "track",
                    provider_track_id,
                    token,
                    os.fspath(partial),
                    os.fspath(partial.with_suffix("")),
                )
            if not partial.is_file() or partial.stat().st_size <= 0:
                raise WorkerError("metadata_unavailable")
            extension = _safe_native_extension(provider, default_format)
            final = partial.with_name(f"native.{extension}")
            os.replace(partial, final)
            declared = _download_media(provider, extension, bitrate)
            return {
                "status": "AVAILABLE",
                "file_path": os.fspath(final),
                **declared,
                "native_encoded": True,
                "provider_decrypted": provider in {"apple_music", "deezer"},
                "upstream_quality_transcoded": False,
            }
        except WorkerError:
            partial.unlink(missing_ok=True)
            raise
        except (KeyError, IndexError) as exc:
            partial.unlink(missing_ok=True)
            raise WorkerError("provider_authentication_error") from exc
        except Exception as exc:
            partial.unlink(missing_ok=True)
            raise WorkerError("metadata_unavailable") from exc

    def _download_destination(self, job_id: str, plan_rank: int) -> Path:
        raw_root = os.environ.get("MUSICBOT_TEMP_DIR")
        if not raw_root or not _JOB_ID.fullmatch(job_id) or not 1 <= plan_rank <= 999:
            raise WorkerError("provider_unavailable")
        root = Path(raw_root).expanduser().resolve()
        job = (root / job_id).resolve()
        source = (job / f"attempt-{plan_rank:03d}" / "source").resolve()
        if job.parent != root or job not in source.parents or not source.is_dir():
            raise WorkerError("provider_unavailable")
        partial = (source / "native.partial").resolve()
        if source not in partial.parents:
            raise WorkerError("provider_unavailable")
        return partial

    @staticmethod
    def _prepare_deezer(token: Any, provider_track_id: str) -> dict[str, Any]:
        api = importlib.import_module("onthespot.api.deezer")
        song = api.get_song_info_from_deezer_website(token, provider_track_id)
        if int(song.get("FILESIZE_FLAC", 0)) > 0:
            return {
                "codec": "flac",
                "container": "flac",
                "lossless": True,
                "provider_decrypted": True,
            }
        if int(song.get("FILESIZE_MP3_320", 0)) > 0:
            return {
                "codec": "mp3",
                "container": "mp3",
                "bitrate_kbps": 320,
                "lossless": False,
                "provider_decrypted": True,
            }
        bitrate = 256 if int(song.get("FILESIZE_MP3_256", 0)) > 0 else 128
        return {
            "codec": "mp3",
            "container": "mp3",
            "bitrate_kbps": bitrate,
            "lossless": False,
            "provider_decrypted": True,
        }

    @staticmethod
    def _prepare_tidal(token: Any, provider_track_id: str) -> dict[str, Any] | None:
        api = importlib.import_module("onthespot.api.tidal")
        manifest = api.tidal_get_mpd_data(token, provider_track_id)
        if not isinstance(manifest, str) or not manifest:
            return None
        lowered = manifest.lower()
        if "flac" in lowered or "audio/flac" in lowered:
            return {"codec": "flac", "container": "flac", "lossless": True}
        if "mp4a" in lowered or "audio/mp4" in lowered or "audio/m4a" in lowered:
            return {"codec": "aac", "container": "m4a", "lossless": False}
        return None

    @staticmethod
    def _prepare_soundcloud(token: Any, provider_track_id: str) -> dict[str, Any] | None:
        if not isinstance(token, Mapping) or not token.get("oauth_token"):
            return {"codec": "mp3", "container": "mp3", "bitrate_kbps": 128, "lossless": False}
        return None

    @staticmethod
    def _source_exception_result(provider: str, exc: Exception) -> dict[str, Any]:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code == 404:
            return _source_result("SOURCE_UNAVAILABLE", "source_unavailable")
        if status_code in {401, 403} and provider in _USER_AUTH_SERVICES:
            return _source_result("AUTH_REQUIRED", "authentication_required")
        return _source_result("ERROR", "provider_error")

    @staticmethod
    def _validate_config_location() -> None:
        configured = os.environ.get("ONTHESPOTDIR")
        if not configured:
            return
        config_dir = Path(configured).expanduser().resolve()
        repository_root = Path(__file__).resolve().parents[3]
        if config_dir == repository_root or repository_root in config_dir.parents:
            raise WorkerError("provider_unavailable")

    @staticmethod
    def _initialization_result() -> dict[str, Any]:
        try:
            version = importlib.metadata.version("onthespot")
        except importlib.metadata.PackageNotFoundError:
            version = None
        return {"protocol": PROTOCOL_VERSION, "version": version}


class _silence_upstream:
    def __init__(self) -> None:
        self._sink: TextIO | None = None
        self._stdout: Any = None
        self._stderr: Any = None

    def __enter__(self) -> None:
        self._sink = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        self._stdout = redirect_stdout(self._sink)
        self._stderr = redirect_stderr(self._sink)
        self._stdout.__enter__()
        self._stderr.__enter__()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._stderr.__exit__(exc_type, exc, traceback)
        self._stdout.__exit__(exc_type, exc, traceback)
        if self._sink is not None:
            self._sink.close()


def _wire_value(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _response(request_id: str | None, *, result: Any = None, error: str | None = None) -> bytes:
    if error is None:
        payload: dict[str, Any] = {"id": request_id, "ok": True, "result": result}
    else:
        payload = {
            "id": request_id,
            "ok": False,
            "error": {"code": error, "message": "OnTheSpot operation failed"},
        }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_MESSAGE_BYTES:
        return _response(request_id, error="metadata_unavailable")
    return encoded + b"\n"


def main() -> int:
    protocol_stdout = sys.stdout.buffer
    worker = OnTheSpotWorker()
    result: Any
    while True:
        line = sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1)
        if not line:
            return 0
        request_id: str | None = None
        if len(line) > MAX_MESSAGE_BYTES or not line.endswith(b"\n"):
            protocol_stdout.write(_response(None, error="provider_unavailable"))
            protocol_stdout.flush()
            return 2
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError
            raw_id = request.get("id")
            if not isinstance(raw_id, str) or not raw_id or len(raw_id) > 128:
                raise ValueError
            request_id = raw_id
            method = request.get("method")
            params = request.get("params")
            if not isinstance(params, dict):
                raise ValueError
            if method == INITIALIZE_METHOD:
                result = worker.initialize()
            elif method == GET_METADATA_METHOD:
                url = params.get("url")
                if not isinstance(url, str):
                    raise WorkerError("invalid_track_url")
                result = worker.get_metadata(url)
            elif method == LIST_SEARCHABLE_PROVIDERS_METHOD:
                result = worker.list_searchable_providers()
            elif method == SEARCH_TRACKS_METHOD:
                provider = params.get("provider")
                query = params.get("query")
                limit = params.get("limit")
                if (
                    not isinstance(provider, str)
                    or not isinstance(query, str)
                    or not isinstance(limit, int)
                ):
                    raise WorkerError("unsupported_provider")
                result = worker.search_tracks(provider, query, limit)
            elif method == CHECK_SOURCE_METHOD:
                provider = params.get("provider")
                provider_track_id = params.get("provider_track_id")
                if not isinstance(provider, str) or not isinstance(provider_track_id, str):
                    raise WorkerError("unsupported_provider")
                result = worker.check_source(provider, provider_track_id)
            elif method == PREPARE_SOURCE_METHOD:
                provider = params.get("provider")
                provider_track_id = params.get("provider_track_id")
                if not isinstance(provider, str) or not isinstance(provider_track_id, str):
                    raise WorkerError("unsupported_provider")
                result = worker.prepare_source(provider, provider_track_id)
            elif method == DOWNLOAD_NATIVE_METHOD:
                provider = params.get("provider")
                provider_track_id = params.get("provider_track_id")
                job_id = params.get("job_id")
                plan_rank = params.get("plan_rank")
                if (
                    not isinstance(provider, str)
                    or not isinstance(provider_track_id, str)
                    or not isinstance(job_id, str)
                    or isinstance(plan_rank, bool)
                    or not isinstance(plan_rank, int)
                ):
                    raise WorkerError("unsupported_provider")
                result = worker.download_native(provider, provider_track_id, job_id, plan_rank)
            elif method == SHUTDOWN_METHOD:
                protocol_stdout.write(_response(request_id, result={"stopped": True}))
                protocol_stdout.flush()
                return 0
            else:
                raise WorkerError("provider_unavailable")
            response = _response(request_id, result=result)
        except WorkerError as exc:
            response = _response(request_id, error=exc.code)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            response = _response(request_id, error="provider_unavailable")
        except Exception:
            response = _response(request_id, error="provider_unavailable")
        protocol_stdout.write(response)
        protocol_stdout.flush()


def _source_result(
    status: str,
    error_code: str | None = None,
    *,
    native: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"status": status}
    if error_code is not None:
        result["error_code"] = error_code
    if native is not None:
        result["native"] = native
    return result


def _native_media(provider: str, account: Mapping[str, Any]) -> dict[str, Any] | None:
    if provider == "apple_music":
        return {"codec": "aac", "container": "m4a", "bitrate_kbps": 256}
    if provider == "bandcamp":
        return {"codec": "mp3", "container": "mp3", "bitrate_kbps": 128}
    if provider == "qobuz":
        return {"codec": "flac", "container": "flac"}
    if provider == "spotify":
        bitrate = _account_bitrate(account)
        return {
            "codec": "vorbis",
            "container": "ogg",
            **({"bitrate_kbps": bitrate} if bitrate in {160, 320} else {}),
        }
    if provider == "youtube_music":
        return {"codec": "aac", "container": "m4a", "bitrate_kbps": 128}
    if provider == "soundcloud" and account.get("account_type") == "public":
        return {"codec": "mp3", "container": "mp3", "bitrate_kbps": 128}
    return None


def _safe_native_extension(provider: str, default_format: object) -> str:
    extension = str(default_format).lower().lstrip(".")
    if extension in {"mp3", "m4a", "flac", "ogg", "webm", "opus"}:
        return extension
    if provider == "spotify":
        return "ogg"
    raise WorkerError("metadata_unavailable")


def _download_media(provider: str, extension: str, bitrate: object) -> dict[str, Any]:
    codec = {
        "mp3": "mp3",
        "m4a": "aac",
        "flac": "flac",
        "ogg": "vorbis",
        "webm": "opus",
        "opus": "opus",
    }.get(extension, "unknown")
    container = {
        "mp3": "mp3",
        "m4a": "m4a",
        "flac": "flac",
        "ogg": "ogg",
        "webm": "webm",
        "opus": "ogg",
    }.get(extension, "unknown")
    raw_bitrate = str(bitrate).lower().removesuffix("k")
    try:
        bitrate_kbps = int(raw_bitrate)
    except ValueError:
        bitrate_kbps = None
    result: dict[str, Any] = {
        "codec": codec,
        "container": container,
        "lossless": codec == "flac",
    }
    if bitrate_kbps is not None and bitrate_kbps > 0 and codec != "flac":
        result["bitrate_kbps"] = bitrate_kbps
    return result


def _account_bitrate(account: Mapping[str, Any]) -> int | None:
    value = account.get("bitrate")
    if not isinstance(value, str) or not value.endswith("k"):
        return None
    try:
        bitrate = int(value[:-1])
    except ValueError:
        return None
    return bitrate if bitrate > 0 else None


def _selected_account(accounts: list[Mapping[str, Any]], token: Any) -> Mapping[str, Any]:
    for account in accounts:
        login = account.get("login")
        if login is token:
            return account
        if isinstance(login, Mapping) and login.get("session") is token:
            return account
    return min(accounts, key=lambda account: str(account.get("uuid", "")))


if __name__ == "__main__":
    raise SystemExit(main())
