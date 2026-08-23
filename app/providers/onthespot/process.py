"""Async lifecycle owner for the isolated OnTheSpot interpreter process."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.core.exceptions import (
    AlbumTooLarge,
    InvalidTrackUrl,
    MetadataUnavailable,
    ProviderAuthenticationError,
    ProviderOperationTimeout,
    ProviderUnavailable,
    TemporaryStorageUnavailable,
    UnsupportedAlbum,
    UnsupportedProvider,
)
from app.core.provider_accounts import ProviderAccountErrorCode, SensitiveValue
from app.providers.base import ProviderAvailability
from app.providers.deezer_authorization import DeezerArlAuthorizationResult
from app.providers.onthespot.ipc import (
    CHECK_PROVIDER_HEALTH_METHOD,
    CHECK_SOURCE_METHOD,
    DEEZER_ARL_AUTHORIZE_METHOD,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DOWNLOAD_NATIVE_METHOD,
    GET_METADATA_METHOD,
    GET_TRACK_METADATA_METHOD,
    INITIALIZE_METHOD,
    LIST_SEARCHABLE_PROVIDERS_METHOD,
    MATCH_URL_METHOD,
    MAX_MESSAGE_BYTES,
    PREPARE_SOURCE_METHOD,
    REFRESH_PROVIDER_HEALTH_METHOD,
    RESOLVE_ALBUM_ID_METHOD,
    RESOLVE_ALBUM_METHOD,
    SEARCH_TRACKS_METHOD,
    SHUTDOWN_METHOD,
    TIDAL_DEVICE_AUTHORIZATION_CANCEL_METHOD,
    TIDAL_DEVICE_AUTHORIZATION_POLL_METHOD,
    TIDAL_DEVICE_AUTHORIZATION_START_METHOD,
)
from app.providers.tidal_authorization import (
    TidalDeviceAuthorizationPoll,
    TidalDeviceAuthorizationStart,
    TidalDevicePollStatus,
)

_TIDAL_FLOW_ID = re.compile(r"^[0-9a-f]{16}$")
_TIDAL_ERROR_CODES = frozenset(
    {
        ProviderAccountErrorCode.TIDAL_AUTH_START_FAILED,
        ProviderAccountErrorCode.TIDAL_AUTH_PENDING,
        ProviderAccountErrorCode.TIDAL_AUTH_SLOW_DOWN,
        ProviderAccountErrorCode.TIDAL_AUTH_DENIED,
        ProviderAccountErrorCode.TIDAL_AUTH_EXPIRED,
        ProviderAccountErrorCode.TIDAL_AUTH_NETWORK_ERROR,
        ProviderAccountErrorCode.TIDAL_AUTH_INVALID_RESPONSE,
        ProviderAccountErrorCode.TIDAL_AUTH_PERSIST_FAILED,
        ProviderAccountErrorCode.TIDAL_AUTH_RELOAD_FAILED,
    }
)
_DEEZER_ERROR_CODES = frozenset(
    {
        ProviderAccountErrorCode.DEEZER_ARL_INVALID_FORMAT,
        ProviderAccountErrorCode.DEEZER_ARL_INVALID,
        ProviderAccountErrorCode.DEEZER_AUTH_NETWORK_ERROR,
        ProviderAccountErrorCode.DEEZER_AUTH_TIMEOUT,
        ProviderAccountErrorCode.DEEZER_AUTH_INVALID_RESPONSE,
        ProviderAccountErrorCode.DEEZER_AUTH_UPSTREAM_ERROR,
        ProviderAccountErrorCode.DEEZER_AUTH_PERSIST_FAILED,
    }
)

_ERROR_TYPES: dict[str, type[Exception]] = {
    "album_too_large": AlbumTooLarge,
    "invalid_track_url": InvalidTrackUrl,
    "metadata_unavailable": MetadataUnavailable,
    "provider_authentication_error": ProviderAuthenticationError,
    "provider_unavailable": ProviderUnavailable,
    "temporary_storage_unavailable": TemporaryStorageUnavailable,
    "unsupported_provider": UnsupportedProvider,
    "unsupported_album": UnsupportedAlbum,
}


class OnTheSpotProcessClient:
    """Own exactly one long-lived worker and serialize its Stage-2 operations."""

    def __init__(
        self,
        *,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        command: Sequence[str] | None = None,
        environment: Mapping[str, str] | None = None,
        temp_dir: Path | None = None,
    ) -> None:
        self._request_timeout = request_timeout
        self._command = tuple(command or (sys.executable, "-m", "app.providers.onthespot.worker"))
        self._environment = dict(environment) if environment is not None else None
        self._temp_dir = (temp_dir or Path("./temp")).expanduser().resolve()
        self._process: asyncio.subprocess.Process | None = None
        self._request_lock = asyncio.Lock()
        self._started_once = False
        self._failed = False
        self._closed = False
        self._version: str | None = None

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def process_id(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def temp_dir(self) -> Path:
        return self._temp_dir

    async def availability(self) -> ProviderAvailability:
        try:
            async with self._request_lock:
                await self._ensure_started_locked()
        except ProviderUnavailable:
            return ProviderAvailability(False, detail="worker_initialization_failed")
        return ProviderAvailability(True, version=self._version)

    async def get_metadata(self, url: str) -> Mapping[str, Any]:
        result = await self._request(GET_METADATA_METHOD, {"url": url})
        if not isinstance(result, dict):
            raise MetadataUnavailable()
        return result

    async def match_url(self, url: str) -> Mapping[str, Any]:
        result = await self._request(MATCH_URL_METHOD, {"url": url})
        if not isinstance(result, dict):
            raise MetadataUnavailable()
        return result

    async def get_track_metadata(self, provider: str, provider_track_id: str) -> Mapping[str, Any]:
        result = await self._request(
            GET_TRACK_METADATA_METHOD,
            {"provider": provider, "provider_track_id": provider_track_id},
        )
        if not isinstance(result, dict):
            raise MetadataUnavailable()
        return result

    async def resolve_album(self, url: str) -> Mapping[str, Any]:
        result = await self._request(RESOLVE_ALBUM_METHOD, {"url": url})
        if not isinstance(result, dict):
            raise MetadataUnavailable()
        return result

    async def resolve_album_id(self, provider: str, provider_album_id: str) -> Mapping[str, Any]:
        result = await self._request(
            RESOLVE_ALBUM_ID_METHOD,
            {"provider": provider, "provider_album_id": provider_album_id},
        )
        if not isinstance(result, dict):
            raise MetadataUnavailable()
        return result

    async def list_searchable_providers(self) -> list[str]:
        result = await self._request(LIST_SEARCHABLE_PROVIDERS_METHOD, {})
        if not isinstance(result, list) or not all(isinstance(value, str) for value in result):
            raise ProviderUnavailable()
        return result

    async def search_tracks(self, provider: str, query: str, limit: int) -> list[Mapping[str, Any]]:
        result = await self._request(
            SEARCH_TRACKS_METHOD,
            {"provider": provider, "query": query, "limit": limit},
        )
        if not isinstance(result, list) or not all(isinstance(value, dict) for value in result):
            raise MetadataUnavailable()
        return result

    async def check_source(self, provider: str, provider_track_id: str) -> Mapping[str, Any]:
        result = await self._request(
            CHECK_SOURCE_METHOD,
            {"provider": provider, "provider_track_id": provider_track_id},
        )
        if not isinstance(result, dict):
            raise ProviderUnavailable()
        return result

    async def check_provider_health(self, provider: str) -> Mapping[str, Any]:
        result = await self._request(CHECK_PROVIDER_HEALTH_METHOD, {"provider": provider})
        if not isinstance(result, dict):
            raise ProviderUnavailable()
        return result

    async def refresh_provider_health(self) -> None:
        result = await self._request(REFRESH_PROVIDER_HEALTH_METHOD, {})
        if not isinstance(result, dict) or result.get("refreshed") is not True:
            raise ProviderUnavailable()

    async def authorize_deezer_arl(
        self, credential: SensitiveValue
    ) -> DeezerArlAuthorizationResult:
        result = await self._request(
            DEEZER_ARL_AUTHORIZE_METHOD,
            {"arl": credential.reveal_to_provider_backend()},
        )
        if not isinstance(result, dict):
            raise ProviderUnavailable()
        if result == {"status": "persisted"}:
            return DeezerArlAuthorizationResult(True)
        if set(result) != {"status", "error_code"} or result.get("status") != "failed":
            raise ProviderUnavailable()
        code = _deezer_error_code(result.get("error_code"))
        return DeezerArlAuthorizationResult(False, code)

    async def start_tidal_device_authorization(self) -> TidalDeviceAuthorizationStart:
        result = await self._request(TIDAL_DEVICE_AUTHORIZATION_START_METHOD, {})
        if not isinstance(result, dict) or not set(result).issubset(
            {"status", "flow_id", "verification_url", "expires_in", "interval", "error_code"}
        ):
            raise ProviderUnavailable()
        status = result.get("status")
        if status == "failed":
            return TidalDeviceAuthorizationStart(
                status="failed", error_code=_tidal_error_code(result.get("error_code"))
            )
        flow_id = result.get("flow_id")
        verification_url = result.get("verification_url")
        expires_in = result.get("expires_in")
        interval = result.get("interval")
        if (
            status != "started"
            or not isinstance(flow_id, str)
            or _TIDAL_FLOW_ID.fullmatch(flow_id) is None
            or not isinstance(verification_url, str)
            or isinstance(expires_in, bool)
            or not isinstance(expires_in, (int, float))
            or isinstance(interval, bool)
            or not isinstance(interval, (int, float))
        ):
            raise ProviderUnavailable()
        return TidalDeviceAuthorizationStart(
            status="started",
            flow_id=flow_id,
            verification_url=verification_url,
            expires_in_seconds=float(expires_in),
            interval_seconds=float(interval),
        )

    async def poll_tidal_device_authorization(self, flow_id: str) -> TidalDeviceAuthorizationPoll:
        if _TIDAL_FLOW_ID.fullmatch(flow_id) is None:
            raise ProviderUnavailable()
        result = await self._request(TIDAL_DEVICE_AUTHORIZATION_POLL_METHOD, {"flow_id": flow_id})
        if not isinstance(result, dict) or not set(result).issubset(
            {"status", "retry_after", "error_code"}
        ):
            raise ProviderUnavailable()
        raw_status = result.get("status")
        raw_retry = result.get("retry_after")
        if not isinstance(raw_status, str) or (
            raw_retry is not None
            and (
                isinstance(raw_retry, bool)
                or not isinstance(raw_retry, (int, float))
                or raw_retry < 0
            )
        ):
            raise ProviderUnavailable()
        try:
            status = TidalDevicePollStatus(raw_status)
        except ValueError as exc:
            raise ProviderUnavailable() from exc
        return TidalDeviceAuthorizationPoll(
            status=status,
            retry_after_seconds=float(raw_retry) if raw_retry is not None else None,
            error_code=_tidal_error_code(result.get("error_code"), required=False),
        )

    async def cancel_tidal_device_authorization(self, flow_id: str) -> None:
        if _TIDAL_FLOW_ID.fullmatch(flow_id) is None:
            raise ProviderUnavailable()
        result = await self._request(TIDAL_DEVICE_AUTHORIZATION_CANCEL_METHOD, {"flow_id": flow_id})
        if (
            not isinstance(result, dict)
            or set(result) != {"status"}
            or result.get("status") not in {"cancelled", "released", "not_found"}
        ):
            raise ProviderUnavailable()

    async def prepare_source(self, provider: str, provider_track_id: str) -> Mapping[str, Any]:
        result = await self._request(
            PREPARE_SOURCE_METHOD,
            {"provider": provider, "provider_track_id": provider_track_id},
        )
        if not isinstance(result, dict):
            raise ProviderUnavailable()
        return result

    async def download_native(
        self,
        provider: str,
        provider_track_id: str,
        job_id: str,
        plan_rank: int,
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        result = await self._request(
            DOWNLOAD_NATIVE_METHOD,
            {
                "provider": provider,
                "provider_track_id": provider_track_id,
                "job_id": job_id,
                "plan_rank": plan_rank,
            },
            timeout_seconds=timeout_seconds,
        )
        if not isinstance(result, dict):
            raise ProviderUnavailable()
        return result

    async def close(self) -> None:
        async with self._request_lock:
            if self._closed:
                return
            self._closed = True
            process = self._process
            if process is None:
                return
            if process.returncode is None:
                try:
                    await self._exchange_locked(SHUTDOWN_METHOD, {})
                except (ProviderUnavailable, MetadataUnavailable):
                    pass
            await self._terminate_locked()

    async def _request(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        async with self._request_lock:
            await self._ensure_started_locked()
            try:
                return await self._exchange_locked(method, params, timeout_seconds=timeout_seconds)
            except asyncio.CancelledError:
                if (
                    method
                    in {
                        CHECK_PROVIDER_HEALTH_METHOD,
                        REFRESH_PROVIDER_HEALTH_METHOD,
                    }
                    and not self._closed
                ):
                    self._failed = False
                    self._started_once = False
                raise
            except ProviderOperationTimeout:
                if (
                    method
                    in {
                        DOWNLOAD_NATIVE_METHOD,
                        CHECK_PROVIDER_HEALTH_METHOD,
                        REFRESH_PROVIDER_HEALTH_METHOD,
                    }
                    and not self._closed
                ):
                    self._failed = False
                    self._started_once = False
                raise

    async def _ensure_started_locked(self) -> None:
        if self._closed or self._failed:
            raise ProviderUnavailable()
        if self._process is not None:
            if self._process.returncode is None:
                return
            self._failed = True
            raise ProviderUnavailable()
        if self._started_once:
            self._failed = True
            raise ProviderUnavailable()

        self._started_once = True
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=self._worker_environment(),
                limit=MAX_MESSAGE_BYTES + 1,
            )
            initialized = await self._exchange_locked(INITIALIZE_METHOD, {})
        except asyncio.CancelledError:
            self._failed = True
            await asyncio.shield(self._terminate_locked())
            raise
        except Exception as exc:
            self._failed = True
            await self._terminate_locked()
            if isinstance(exc, ProviderUnavailable):
                raise
            raise ProviderUnavailable() from exc

        if not isinstance(initialized, dict) or initialized.get("protocol") != 1:
            self._failed = True
            await self._terminate_locked()
            raise ProviderUnavailable()
        version = initialized.get("version")
        self._version = str(version) if version is not None else None

    async def _exchange_locked(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise ProviderUnavailable()
        if process.returncode is not None:
            self._failed = True
            raise ProviderUnavailable()

        request_id = uuid.uuid4().hex
        payload = json.dumps(
            {"id": request_id, "method": method, "params": dict(params)},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > MAX_MESSAGE_BYTES:
            raise MetadataUnavailable()

        try:
            process.stdin.write(payload + b"\n")
            async with asyncio.timeout(timeout_seconds or self._request_timeout):
                await process.stdin.drain()
                response_line = await process.stdout.readline()
        except asyncio.CancelledError:
            self._failed = True
            await asyncio.shield(self._terminate_locked())
            raise
        except TimeoutError as exc:
            self._failed = True
            await self._terminate_locked()
            raise ProviderOperationTimeout() from exc
        except (BrokenPipeError, ConnectionError, OSError, ValueError) as exc:
            self._failed = True
            await self._terminate_locked()
            raise ProviderUnavailable() from exc

        if not response_line or len(response_line) > MAX_MESSAGE_BYTES:
            self._failed = True
            await self._terminate_locked()
            raise ProviderUnavailable()
        try:
            response = json.loads(response_line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._failed = True
            await self._terminate_locked()
            raise ProviderUnavailable() from exc
        if not isinstance(response, dict) or response.get("id") != request_id:
            self._failed = True
            await self._terminate_locked()
            raise ProviderUnavailable()
        if response.get("ok") is True:
            return response.get("result")

        error = response.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        error_type = _ERROR_TYPES.get(str(code), MetadataUnavailable)
        raise error_type()

    async def _terminate_locked(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
        if process.returncode is None:
            process.terminate()
            try:
                async with asyncio.timeout(5):
                    await process.wait()
            except TimeoutError:
                process.kill()
                await process.wait()

    def _worker_environment(self) -> dict[str, str]:
        environment = dict(os.environ if self._environment is None else self._environment)
        environment["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
        environment["LOG_LEVEL"] = "20"
        environment["PYTHONUNBUFFERED"] = "1"
        environment["MUSICBOT_TEMP_DIR"] = os.fspath(self._temp_dir)
        return environment


_shared_client: OnTheSpotProcessClient | None = None


def get_shared_process_client() -> OnTheSpotProcessClient:
    global _shared_client
    if _shared_client is None or _shared_client.closed:
        from app.config import get_settings

        _shared_client = OnTheSpotProcessClient(temp_dir=get_settings().temp_dir)
    return _shared_client


async def close_shared_process_client() -> None:
    global _shared_client
    client, _shared_client = _shared_client, None
    if client is not None:
        await client.close()


def _tidal_error_code(value: object, *, required: bool = True) -> ProviderAccountErrorCode | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        return ProviderAccountErrorCode.TIDAL_AUTH_INVALID_RESPONSE
    try:
        code = ProviderAccountErrorCode(value)
    except ValueError:
        return ProviderAccountErrorCode.TIDAL_AUTH_INVALID_RESPONSE
    return (
        code if code in _TIDAL_ERROR_CODES else ProviderAccountErrorCode.TIDAL_AUTH_INVALID_RESPONSE
    )


def _deezer_error_code(value: object) -> ProviderAccountErrorCode:
    if not isinstance(value, str):
        return ProviderAccountErrorCode.DEEZER_AUTH_INVALID_RESPONSE
    try:
        code = ProviderAccountErrorCode(value)
    except ValueError:
        return ProviderAccountErrorCode.DEEZER_AUTH_INVALID_RESPONSE
    return (
        code
        if code in _DEEZER_ERROR_CODES
        else ProviderAccountErrorCode.DEEZER_AUTH_INVALID_RESPONSE
    )
