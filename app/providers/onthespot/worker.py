"""Isolated OnTheSpot JSON Lines worker.

This module intentionally sets compatibility environment variables before any
third-party provider import. Protocol output is the original stdout stream;
upstream stdout and stderr are discarded so they cannot corrupt framing or
reach parent application logs.
"""

from __future__ import annotations

import errno
import os

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
os.environ["LOG_LEVEL"] = "20"

import importlib
import importlib.metadata
import json
import re
import sys
import time
import uuid
from collections.abc import Mapping
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlsplit

from app.core.enums import MusicProviderName
from app.providers.onthespot.capabilities import ONTHESPOT_CAPABILITIES
from app.providers.onthespot.ipc import (
    CHECK_PROVIDER_HEALTH_METHOD,
    CHECK_SOURCE_METHOD,
    DEEZER_ARL_AUTHORIZE_METHOD,
    DOWNLOAD_NATIVE_METHOD,
    GET_METADATA_METHOD,
    GET_TRACK_METADATA_METHOD,
    INITIALIZE_METHOD,
    LIST_SEARCHABLE_PROVIDERS_METHOD,
    MATCH_URL_METHOD,
    MAX_MESSAGE_BYTES,
    PREPARE_SOURCE_METHOD,
    PROTOCOL_VERSION,
    REFRESH_PROVIDER_HEALTH_METHOD,
    RESOLVE_ALBUM_ID_METHOD,
    RESOLVE_ALBUM_METHOD,
    SEARCH_TRACKS_METHOD,
    SHUTDOWN_METHOD,
    TIDAL_DEVICE_AUTHORIZATION_CANCEL_METHOD,
    TIDAL_DEVICE_AUTHORIZATION_POLL_METHOD,
    TIDAL_DEVICE_AUTHORIZATION_START_METHOD,
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
        "album_artists",
        "artists",
        "disc_number",
        "explicit",
        "is_playable",
        "isrc",
        "item_url",
        "item_id",
        "length",
        "release_date",
        "release_year",
        "title",
        "track_number",
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
MAX_ALBUM_TRACKS = 500
_MAX_ALBUM_TEXT_LENGTH = 1024
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
_TIDAL_FLOW_ID = re.compile(r"^[0-9a-f]{16}$")
_TIDAL_HTTP_TIMEOUT = (5.0, 10.0)
_TIDAL_DEFAULT_EXPIRY_SECONDS = 300
_TIDAL_MAX_EXPIRY_SECONDS = 900
_TIDAL_DEFAULT_INTERVAL_SECONDS = 3
_TIDAL_MAX_INTERVAL_SECONDS = 30
_DEEZER_GATEWAY_URL = "https://www.deezer.com/ajax/gw-light.php"
_DEEZER_HTTP_TIMEOUT = (5.0, 10.0)
_DEEZER_ARL_MAX_LENGTH = 2048
_DEEZER_COOKIE_VALUE = re.compile(r"^[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]+$")


@dataclass(slots=True)
class _TidalDeviceFlow:
    device_code: str = field(repr=False)
    expires_at_monotonic: float
    interval_seconds: float
    next_poll_at_monotonic: float
    terminal_status: str | None = None
    terminal_error_code: str | None = None


@dataclass(slots=True)
class _DeezerValidatedSession:
    session: Any = field(repr=False)
    api_token: str = field(repr=False)
    license_token: str = field(repr=False)
    account_type: str
    bitrate: str


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
        self._tidal_device_flows: dict[str, _TidalDeviceFlow] = {}

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
                self._install_secure_deezer_login_adapter()
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

        return self.get_track_metadata(str(service), str(item_id))

    def match_url(self, url: str) -> dict[str, str]:
        if not self._initialized:
            self.initialize()
        if not url or len(url) > 2048:
            raise WorkerError("invalid_track_url")
        try:
            with _silence_upstream():
                resolved = self._parse.UrlMatcher().match(url)
        except Exception as exc:
            raise WorkerError("metadata_unavailable") from exc
        if resolved is None:
            raise WorkerError("invalid_track_url")
        service, item_type, item_id = resolved
        if service == "__handled__" or not item_type or not item_id:
            raise WorkerError("invalid_track_url")
        return {"service": str(service), "item_type": str(item_type), "item_id": str(item_id)}

    def get_track_metadata(self, service: str, item_id: str) -> dict[str, Any]:
        if not self._initialized:
            self.initialize()
        if service not in _DOWNLOAD_SERVICES or not item_id or len(item_id) > 2048:
            raise WorkerError("unsupported_provider")
        raw = self._raw_track_metadata(service, item_id)
        return {
            "service": service,
            "item_type": "track",
            "item_id": str(item_id),
            "metadata": _wire_metadata(raw),
        }

    def resolve_album(self, url: str) -> dict[str, Any]:
        matched = self.match_url(url)
        service = matched["service"]
        item_type = matched["item_type"]
        album_id = matched["item_id"]
        if item_type != "album":
            raise WorkerError("unsupported_album")
        return self.resolve_album_id(service, album_id)

    def resolve_album_id(self, service: str, album_id: str) -> dict[str, Any]:
        if service not in _DOWNLOAD_SERVICES or not album_id or len(album_id) > 2048:
            raise WorkerError("unsupported_album")
        get_track_ids = self._registry.SERVICE_ALBUM_TRACK_ID_FUNCTIONS.get(service)
        if get_track_ids is None:
            raise WorkerError("unsupported_album")
        try:
            with _silence_upstream():
                token = self._accounts.get_account_token(service)
                if service in _AUTHENTICATED_SERVICES and token is None:
                    raise WorkerError("provider_authentication_error")
                raw_ids = get_track_ids(token, album_id)
        except WorkerError:
            raise
        except (KeyError, IndexError) as exc:
            raise WorkerError("provider_authentication_error") from exc
        except Exception as exc:
            raise WorkerError("metadata_unavailable") from exc
        if not isinstance(raw_ids, list) or not raw_ids:
            raise WorkerError("metadata_unavailable")
        if len(raw_ids) > MAX_ALBUM_TRACKS:
            raise WorkerError("album_too_large")

        tracks: list[dict[str, Any]] = []
        album_title: str | None = None
        album_artist: str | None = None
        release_date: str | None = None
        durations_complete = True
        duration_total = 0
        for position, value in enumerate(raw_ids, start=1):
            track_id = str(value).strip()
            if not track_id or len(track_id) > 2048:
                raise WorkerError("metadata_unavailable")
            try:
                raw = self._raw_track_metadata(service, track_id)
            except WorkerError:
                raw = {}
            title = _album_text(raw.get("title"))
            artist = _album_text(raw.get("artists"))
            album_title = album_title or _album_text(raw.get("album_name"))
            album_artist = album_artist or _album_text(raw.get("album_artists")) or artist
            release_date = release_date or _album_text(
                raw.get("release_date") or raw.get("release_year")
            )
            duration = _album_positive_int(raw.get("length"))
            if duration is None:
                durations_complete = False
            else:
                duration_total += duration
            tracks.append(
                {
                    "provider_track_id": track_id,
                    "position": position,
                    "title": title,
                    "artist": artist,
                    "disc_number": _album_positive_int(raw.get("disc_number")),
                    "track_number": _album_positive_int(raw.get("track_number")),
                    "duration_ms": duration,
                    "explicit": raw.get("explicit")
                    if isinstance(raw.get("explicit"), bool)
                    else None,
                }
            )
        if album_title is None or album_artist is None:
            raise WorkerError("metadata_unavailable")
        return {
            "provider": service,
            "provider_album_id": album_id,
            "title": album_title,
            "artist": album_artist,
            "release_date": release_date,
            "duration_ms": duration_total if durations_complete else None,
            "tracks": tracks,
        }

    def _raw_track_metadata(self, service: str, item_id: str) -> Mapping[str, Any]:
        try:
            with _silence_upstream():
                token = self._accounts.get_account_token(service)
                if service in _AUTHENTICATED_SERVICES and token is None:
                    raise WorkerError("provider_authentication_error")
                metadata_function = self._registry.get_metadata_function(service, "track")
                raw = metadata_function(token, item_id)
        except WorkerError:
            raise
        except (KeyError, IndexError) as exc:
            raise WorkerError("provider_authentication_error") from exc
        except Exception as exc:
            raise WorkerError("metadata_unavailable") from exc
        if not isinstance(raw, Mapping) or not raw:
            raise WorkerError("metadata_unavailable")
        return raw

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

    def check_provider_health(self, provider: str) -> dict[str, Any]:
        """Inspect current runtime account/session facts without selecting a TrackSource."""

        if not self._initialized:
            self.initialize()
        try:
            capabilities = ONTHESPOT_CAPABILITIES[MusicProviderName(provider)]
        except (KeyError, ValueError):
            return _health_result("UNAVAILABLE", False, False, "RUNTIME_UNAVAILABLE")

        requires_auth = bool(capabilities.requires_auth)
        if not capabilities.download_supported:
            return _health_result("UNAVAILABLE", requires_auth, False, "RUNTIME_UNAVAILABLE")
        configured = any(
            isinstance(account, Mapping)
            and account.get("service") == provider
            and account.get("active") is True
            for account in self._config.get("accounts", [])
        )
        active_accounts = [
            account
            for account in self._runtime.account_pool
            if isinstance(account, Mapping)
            and account.get("service") == provider
            and account.get("status") == "active"
        ]
        if not active_accounts:
            if requires_auth:
                code = "SESSION_UNAVAILABLE" if configured else "AUTH_NOT_CONFIGURED"
                return _health_result("AUTH_REQUIRED", True, True, code)
            return _health_result("UNAVAILABLE", False, True, "RUNTIME_UNAVAILABLE")

        try:
            with _silence_upstream():
                token = self._accounts.get_account_token(provider)
        except (KeyError, IndexError):
            status = "AUTH_REQUIRED" if requires_auth else "UNAVAILABLE"
            code = "SESSION_UNAVAILABLE" if requires_auth else "RUNTIME_UNAVAILABLE"
            return _health_result(status, requires_auth, True, code)
        except Exception:
            return _health_result("ERROR", requires_auth, True, "UPSTREAM_ERROR")

        if provider in _AUTHENTICATED_SERVICES and token is None:
            status = "AUTH_REQUIRED" if requires_auth else "UNAVAILABLE"
            code = "SESSION_UNAVAILABLE" if requires_auth else "RUNTIME_UNAVAILABLE"
            return _health_result(status, requires_auth, True, code)

        selected = _selected_account(active_accounts, token)
        if provider == "apple_music" and selected.get("account_type") != "premium":
            return _health_result("AUTH_REQUIRED", True, True, "SUBSCRIPTION_REQUIRED")
        if provider == "qobuz":
            # v1.8.1 login only pings qobuz.com and trusts the saved token.
            return _health_result("UNKNOWN", True, True, "SESSION_UNVERIFIED")
        return _health_result("READY", requires_auth, True)

    def refresh_provider_health(self) -> dict[str, bool]:
        """Reload deployment-owned config and rebuild the one serialized runtime pool."""

        if not self._initialized:
            self.initialize()
        try:
            with _silence_upstream():
                config_module = importlib.import_module("onthespot.otsconfig")
                fresh_config = config_module.Config()
                fresh_accounts = fresh_config.get("accounts")
                if not isinstance(fresh_accounts, list):
                    raise ValueError
                self._config.set("accounts", fresh_accounts)
                self._config.set(
                    "active_account_number", fresh_config.get("active_account_number", 0)
                )
                for runtime_account in self._runtime.account_pool:
                    if not isinstance(runtime_account, Mapping):
                        continue
                    login = runtime_account.get("login")
                    session = login.get("session") if isinstance(login, Mapping) else None
                    close = getattr(session, "close", None)
                    if callable(close):
                        close()
                self._runtime.account_pool.clear()
                self._install_secure_deezer_login_adapter()
                self._accounts.AccountPoolLoader(gui=False).run()
        except Exception as exc:
            raise WorkerError("provider_unavailable") from exc
        return {"refreshed": True}

    def deezer_arl_authorize(self, arl: str) -> dict[str, str]:
        """Validate through HTTPS before writing an OnTheSpot-owned account record."""

        if not self._initialized:
            self.initialize()
        normalized = _normalize_deezer_arl(arl)
        if normalized is None:
            return _deezer_failure("DEEZER_ARL_INVALID_FORMAT")
        validated, error_code = self._open_secure_deezer_session(normalized)
        if validated is None:
            return _deezer_failure(error_code or "DEEZER_AUTH_INVALID_RESPONSE")
        close = getattr(validated.session, "close", None)
        if callable(close):
            close()

        current = self._config.get("accounts", [])
        if not isinstance(current, list):
            return _deezer_failure("DEEZER_AUTH_PERSIST_FAILED")
        updated = list(current)
        duplicate_index: int | None = None
        for index, account in enumerate(updated):
            if not isinstance(account, Mapping) or account.get("service") != "deezer":
                continue
            login = account.get("login")
            if isinstance(login, Mapping) and login.get("arl") == normalized:
                duplicate_index = index
                break
        if duplicate_index is not None:
            duplicate = updated[duplicate_index]
            if isinstance(duplicate, Mapping) and duplicate.get("active") is True:
                return {"status": "persisted"}
            activated = dict(duplicate)
            activated["active"] = True
            updated[duplicate_index] = activated
        else:
            updated.append(
                {
                    "uuid": str(uuid.uuid4()),
                    "service": "deezer",
                    "active": True,
                    "login": {"arl": normalized},
                }
            )
        try:
            self._config.set("accounts", updated)
            self._config.save()
        except Exception:
            try:
                self._config.set("accounts", current)
            except Exception:
                pass
            return _deezer_failure("DEEZER_AUTH_PERSIST_FAILED")
        return {"status": "persisted"}

    def _install_secure_deezer_login_adapter(self) -> None:
        """Keep every configured Deezer runtime login on the HTTPS-only adapter."""

        login_functions = getattr(self._registry, "SERVICE_LOGIN_FUNCTIONS", None)
        if not isinstance(login_functions, dict):
            raise ValueError
        login_functions["deezer"] = self._secure_deezer_login

    def _secure_deezer_login(self, account: Mapping[str, Any]) -> bool:
        login = account.get("login")
        raw_arl = login.get("arl") if isinstance(login, Mapping) else None
        normalized = _normalize_deezer_arl(raw_arl) if isinstance(raw_arl, str) else None
        if normalized is None:
            self._append_deezer_runtime_error(account)
            return False
        validated, _ = self._open_secure_deezer_session(normalized)
        if validated is None:
            self._append_deezer_runtime_error(account)
            return False
        self._runtime.account_pool.append(
            {
                "uuid": str(account.get("uuid", "")),
                "username": "",
                "service": "deezer",
                "status": "active",
                "account_type": validated.account_type,
                "bitrate": validated.bitrate,
                "login": {
                    "arl": normalized,
                    "api_token": validated.api_token,
                    "license_token": validated.license_token,
                    "session": validated.session,
                },
            }
        )
        return True

    def _open_secure_deezer_session(
        self, arl: str
    ) -> tuple[_DeezerValidatedSession | None, str | None]:
        deezer = importlib.import_module("onthespot.api.deezer")
        session = deezer.requests.Session()
        session.headers.update(
            {
                "Origin": "https://www.deezer.com",
                "Accept-Encoding": "utf-8",
                "Referer": "https://www.deezer.com/login",
            }
        )
        session.cookies.update({"arl": arl, "comeback": "1"})
        try:
            response = session.post(
                _DEEZER_GATEWAY_URL,
                params={
                    "api_version": "1.0",
                    "api_token": "null",
                    "input": "3",
                    "method": "deezer.getUserData",
                },
                timeout=_DEEZER_HTTP_TIMEOUT,
            )
        except deezer.requests.exceptions.Timeout:
            session.close()
            return None, "DEEZER_AUTH_TIMEOUT"
        except deezer.requests.exceptions.RequestException:
            session.close()
            return None, "DEEZER_AUTH_NETWORK_ERROR"
        except Exception:
            session.close()
            return None, "DEEZER_AUTH_NETWORK_ERROR"
        if response.status_code in {401, 403}:
            session.close()
            return None, "DEEZER_ARL_INVALID"
        if response.status_code != 200:
            session.close()
            return None, "DEEZER_AUTH_UPSTREAM_ERROR"
        try:
            payload = response.json()
        except Exception:
            session.close()
            return None, "DEEZER_AUTH_INVALID_RESPONSE"
        parsed = _parse_deezer_user_data(payload)
        if isinstance(parsed, str):
            session.close()
            return None, parsed
        api_token, license_token, account_type, bitrate = parsed
        return (
            _DeezerValidatedSession(session, api_token, license_token, account_type, bitrate),
            None,
        )

    def _append_deezer_runtime_error(self, account: Mapping[str, Any]) -> None:
        self._runtime.account_pool.append(
            {
                "uuid": str(account.get("uuid", "")),
                "username": "",
                "service": "deezer",
                "status": "error",
                "account_type": "N/A",
                "bitrate": "N/A",
                "login": {
                    "arl": "",
                    "api_token": "",
                    "license_token": "",
                    "session": "",
                },
            }
        )

    def tidal_device_authorization_start(self) -> dict[str, Any]:
        """Create one bounded Tidal device challenge without exposing its device code."""

        if not self._initialized:
            self.initialize()
        self._expire_tidal_device_flows()
        try:
            with _silence_upstream():
                tidal = importlib.import_module("onthespot.api.tidal")
                response = tidal.requests.post(
                    f"{tidal.AUTH_URL}/device_authorization",
                    data={"client_id": tidal.CLIENT_ID, "scope": "r_usr+w_usr+w_sub"},
                    timeout=_TIDAL_HTTP_TIMEOUT,
                )
        except Exception:
            return _tidal_start_failure("TIDAL_AUTH_NETWORK_ERROR")
        if response.status_code != 200:
            return _tidal_start_failure("TIDAL_AUTH_START_FAILED")
        try:
            payload = response.json()
        except Exception:
            return _tidal_start_failure("TIDAL_AUTH_INVALID_RESPONSE")
        if not isinstance(payload, Mapping):
            return _tidal_start_failure("TIDAL_AUTH_INVALID_RESPONSE")
        device_code = payload.get("deviceCode")
        verification_url = payload.get("verificationUriComplete")
        if (
            not isinstance(device_code, str)
            or not device_code
            or len(device_code) > 2048
            or not isinstance(verification_url, str)
            or not _valid_tidal_verification_url(verification_url)
        ):
            return _tidal_start_failure("TIDAL_AUTH_INVALID_RESPONSE")
        expires_in = _bounded_number(
            payload.get("expiresIn"),
            default=_TIDAL_DEFAULT_EXPIRY_SECONDS,
            minimum=1,
            maximum=_TIDAL_MAX_EXPIRY_SECONDS,
        )
        interval = _bounded_number(
            payload.get("interval"),
            default=_TIDAL_DEFAULT_INTERVAL_SECONDS,
            minimum=1,
            maximum=_TIDAL_MAX_INTERVAL_SECONDS,
        )
        now = time.monotonic()
        flow_id = uuid.uuid4().hex[:16]
        self._tidal_device_flows[flow_id] = _TidalDeviceFlow(
            device_code=device_code,
            expires_at_monotonic=now + expires_in,
            interval_seconds=interval,
            next_poll_at_monotonic=now + interval,
        )
        return {
            "status": "started",
            "flow_id": flow_id,
            "verification_url": verification_url,
            "expires_in": expires_in,
            "interval": interval,
        }

    def tidal_device_authorization_poll(self, flow_id: str) -> dict[str, Any]:
        """Perform at most one token-endpoint request for an existing device flow."""

        flow = self._tidal_device_flows.get(flow_id)
        if flow is None:
            return _tidal_poll_result("expired", "TIDAL_AUTH_EXPIRED")
        if flow.terminal_status is not None:
            return _tidal_poll_result(flow.terminal_status, flow.terminal_error_code)
        now = time.monotonic()
        if now >= flow.expires_at_monotonic:
            flow.terminal_status = "expired"
            flow.terminal_error_code = "TIDAL_AUTH_EXPIRED"
            return _tidal_poll_result(flow.terminal_status, flow.terminal_error_code)
        if now < flow.next_poll_at_monotonic:
            return _tidal_poll_result(
                "pending", retry_after=max(0.0, flow.next_poll_at_monotonic - now)
            )

        try:
            with _silence_upstream():
                tidal = importlib.import_module("onthespot.api.tidal")
                response = tidal.requests.post(
                    f"{tidal.AUTH_URL}/token",
                    data={
                        "client_id": tidal.CLIENT_ID,
                        "device_code": flow.device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "scope": "r_usr+w_usr+w_sub",
                    },
                    auth=tidal.AUTH,
                    timeout=_TIDAL_HTTP_TIMEOUT,
                )
        except Exception:
            flow.next_poll_at_monotonic = time.monotonic() + flow.interval_seconds
            return _tidal_poll_result(
                "network_error",
                "TIDAL_AUTH_NETWORK_ERROR",
                retry_after=flow.interval_seconds,
            )

        flow.next_poll_at_monotonic = time.monotonic() + flow.interval_seconds
        try:
            payload = response.json()
        except Exception:
            return self._terminal_tidal_poll(
                flow, "invalid_response", "TIDAL_AUTH_INVALID_RESPONSE"
            )
        if not isinstance(payload, Mapping):
            return self._terminal_tidal_poll(
                flow, "invalid_response", "TIDAL_AUTH_INVALID_RESPONSE"
            )
        if response.status_code != 200:
            error = payload.get("error")
            if error == "authorization_pending":
                return _tidal_poll_result("pending", retry_after=flow.interval_seconds)
            if error == "slow_down" or response.status_code == 429:
                flow.interval_seconds = min(flow.interval_seconds + 5, _TIDAL_MAX_INTERVAL_SECONDS)
                flow.next_poll_at_monotonic = time.monotonic() + flow.interval_seconds
                return _tidal_poll_result(
                    "slow_down",
                    "TIDAL_AUTH_SLOW_DOWN",
                    retry_after=flow.interval_seconds,
                )
            if error == "access_denied":
                return self._terminal_tidal_poll(flow, "denied", "TIDAL_AUTH_DENIED")
            if error in {"expired_token", "invalid_grant"}:
                return self._terminal_tidal_poll(flow, "expired", "TIDAL_AUTH_EXPIRED")
            if response.status_code >= 500:
                return _tidal_poll_result(
                    "network_error",
                    "TIDAL_AUTH_NETWORK_ERROR",
                    retry_after=flow.interval_seconds,
                )
            return self._terminal_tidal_poll(
                flow, "invalid_response", "TIDAL_AUTH_INVALID_RESPONSE"
            )

        account = _tidal_account_from_token_payload(payload)
        if account is None:
            return self._terminal_tidal_poll(
                flow, "invalid_response", "TIDAL_AUTH_INVALID_RESPONSE"
            )
        try:
            accounts = self._config.get("accounts")
            if not isinstance(accounts, list):
                raise ValueError
            updated_accounts = accounts.copy()
            updated_accounts.append(account)
            self._config.set("accounts", updated_accounts)
            try:
                self._config.save()
            except Exception:
                self._config.set("accounts", accounts)
                raise
        except Exception:
            return self._terminal_tidal_poll(flow, "persist_failed", "TIDAL_AUTH_PERSIST_FAILED")
        return self._terminal_tidal_poll(flow, "approved")

    def tidal_device_authorization_cancel(self, flow_id: str) -> dict[str, str]:
        """Release pending state only; persisted accounts are never touched."""

        flow = self._tidal_device_flows.pop(flow_id, None)
        if flow is None:
            return {"status": "not_found"}
        if flow.terminal_status == "approved":
            return {"status": "released"}
        return {"status": "cancelled"}

    def _terminal_tidal_poll(
        self, flow: _TidalDeviceFlow, status: str, error_code: str | None = None
    ) -> dict[str, Any]:
        flow.terminal_status = status
        flow.terminal_error_code = error_code
        return _tidal_poll_result(status, error_code)

    def _expire_tidal_device_flows(self) -> None:
        now = time.monotonic()
        expired = [
            flow_id
            for flow_id, flow in self._tidal_device_flows.items()
            if flow.terminal_status != "approved" and now >= flow.expires_at_monotonic
        ]
        for flow_id in expired:
            self._tidal_device_flows.pop(flow_id, None)

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
        except OSError as exc:
            partial.unlink(missing_ok=True)
            if exc.errno == errno.ENOSPC:
                raise WorkerError("temporary_storage_unavailable") from exc
            raise WorkerError("metadata_unavailable") from exc
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


def _wire_metadata(raw: Mapping[str, Any]) -> dict[str, str | int | float | bool]:
    return {
        key: value
        for key in _WIRE_METADATA_KEYS
        if key in raw and (value := _wire_value(raw[key])) is not None
    }


def _album_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > _MAX_ALBUM_TEXT_LENGTH:
        raise WorkerError("metadata_unavailable")
    return text


def _album_positive_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _bounded_number(value: object, *, default: int, minimum: int, maximum: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(default)
    return float(min(max(value, minimum), maximum))


def _valid_tidal_verification_url(value: str) -> bool:
    if not value or len(value) > 2048:
        return False
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "tidal.com" or host.endswith(".tidal.com"))


def _tidal_start_failure(error_code: str) -> dict[str, str]:
    return {"status": "failed", "error_code": error_code}


def _tidal_poll_result(
    status: str,
    error_code: str | None = None,
    *,
    retry_after: float | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"status": status}
    if error_code is not None:
        result["error_code"] = error_code
    if retry_after is not None:
        result["retry_after"] = retry_after
    return result


def _normalize_deezer_arl(value: str) -> str | None:
    if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    normalized = value.strip(" ")
    if (
        len(normalized) < 8
        or len(normalized) > _DEEZER_ARL_MAX_LENGTH
        or _DEEZER_COOKIE_VALUE.fullmatch(normalized) is None
    ):
        return None
    return normalized


def _parse_deezer_user_data(
    payload: object,
) -> tuple[str, str, str, str] | str:
    if not isinstance(payload, Mapping):
        return "DEEZER_AUTH_INVALID_RESPONSE"
    results = payload.get("results")
    if not isinstance(results, Mapping):
        return "DEEZER_AUTH_INVALID_RESPONSE"
    user = results.get("USER")
    if not isinstance(user, Mapping):
        return "DEEZER_AUTH_INVALID_RESPONSE"
    user_id = user.get("USER_ID")
    if isinstance(user_id, bool) or not isinstance(user_id, (int, str)):
        return "DEEZER_AUTH_INVALID_RESPONSE"
    try:
        authenticated_user_id = int(user_id)
    except (TypeError, ValueError):
        return "DEEZER_AUTH_INVALID_RESPONSE"
    if authenticated_user_id <= 0:
        return "DEEZER_ARL_INVALID"
    options = user.get("OPTIONS")
    api_token = results.get("checkForm")
    if not isinstance(options, Mapping) or not isinstance(api_token, str) or not api_token:
        return "DEEZER_AUTH_INVALID_RESPONSE"
    license_token = options.get("license_token")
    if not isinstance(license_token, str) or not license_token:
        return "DEEZER_AUTH_INVALID_RESPONSE"
    if options.get("web_lossless"):
        account_type, bitrate = "premium", "1411k"
    elif options.get("web_hq"):
        account_type, bitrate = "premium", "320k"
    else:
        account_type, bitrate = "free", "128k"
    return api_token, license_token, account_type, bitrate


def _deezer_failure(error_code: str) -> dict[str, str]:
    return {"status": "failed", "error_code": error_code}


def _tidal_account_from_token_payload(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    user = payload.get("user")
    if (
        not isinstance(access_token, str)
        or not access_token
        or not isinstance(refresh_token, str)
        or not refresh_token
        or isinstance(expires_in, bool)
        or not isinstance(expires_in, (int, float))
        or expires_in <= 0
        or not isinstance(user, Mapping)
    ):
        return None
    username = user.get("username")
    country_code = user.get("countryCode")
    if (
        not isinstance(username, str)
        or not username
        or not isinstance(country_code, str)
        or not country_code
    ):
        return None
    return {
        "uuid": str(uuid.uuid4()),
        "service": "tidal",
        "active": True,
        "login": {
            "username": username,
            "country_code": country_code,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_expiry": expires_in + time.time(),
        },
    }


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
            elif method == MATCH_URL_METHOD:
                url = params.get("url")
                if not isinstance(url, str):
                    raise WorkerError("invalid_track_url")
                result = worker.match_url(url)
            elif method == GET_METADATA_METHOD:
                url = params.get("url")
                if not isinstance(url, str):
                    raise WorkerError("invalid_track_url")
                result = worker.get_metadata(url)
            elif method == GET_TRACK_METADATA_METHOD:
                provider = params.get("provider")
                provider_track_id = params.get("provider_track_id")
                if not isinstance(provider, str) or not isinstance(provider_track_id, str):
                    raise WorkerError("unsupported_provider")
                result = worker.get_track_metadata(provider, provider_track_id)
            elif method == RESOLVE_ALBUM_METHOD:
                url = params.get("url")
                if not isinstance(url, str):
                    raise WorkerError("invalid_track_url")
                result = worker.resolve_album(url)
            elif method == RESOLVE_ALBUM_ID_METHOD:
                provider = params.get("provider")
                provider_album_id = params.get("provider_album_id")
                if not isinstance(provider, str) or not isinstance(provider_album_id, str):
                    raise WorkerError("unsupported_album")
                result = worker.resolve_album_id(provider, provider_album_id)
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
            elif method == CHECK_PROVIDER_HEALTH_METHOD:
                provider = params.get("provider")
                if not isinstance(provider, str):
                    raise WorkerError("unsupported_provider")
                result = worker.check_provider_health(provider)
            elif method == REFRESH_PROVIDER_HEALTH_METHOD:
                result = worker.refresh_provider_health()
            elif method == DEEZER_ARL_AUTHORIZE_METHOD:
                arl = params.get("arl")
                if set(params) != {"arl"} or not isinstance(arl, str):
                    raise WorkerError("provider_unavailable")
                result = worker.deezer_arl_authorize(arl)
            elif method == TIDAL_DEVICE_AUTHORIZATION_START_METHOD:
                if params:
                    raise WorkerError("provider_unavailable")
                result = worker.tidal_device_authorization_start()
            elif method == TIDAL_DEVICE_AUTHORIZATION_POLL_METHOD:
                flow_id = params.get("flow_id")
                if not isinstance(flow_id, str) or _TIDAL_FLOW_ID.fullmatch(flow_id) is None:
                    raise WorkerError("provider_unavailable")
                result = worker.tidal_device_authorization_poll(flow_id)
            elif method == TIDAL_DEVICE_AUTHORIZATION_CANCEL_METHOD:
                flow_id = params.get("flow_id")
                if not isinstance(flow_id, str) or _TIDAL_FLOW_ID.fullmatch(flow_id) is None:
                    raise WorkerError("provider_unavailable")
                result = worker.tidal_device_authorization_cancel(flow_id)
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


def _health_result(
    status: str,
    requires_authentication: bool,
    download_supported: bool,
    error_code: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "requires_authentication": requires_authentication,
        "download_supported": download_supported,
    }
    if error_code is not None:
        result["error_code"] = error_code
    return result


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
