from __future__ import annotations

import importlib
import inspect
import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import requests

import app.providers.onthespot.worker as worker_module
from app.config import Settings
from app.core.enums import MusicProviderName
from app.core.provider_accounts import (
    ProviderAccountComponent,
    ProviderAccountComponentStatus,
    ProviderAccountErrorCode,
    ProviderAccountState,
    ProviderAccountStatus,
    ProviderAuthorizationMethod,
    ProviderAuthorizationOutcomeStatus,
    ProviderAuthorizationRequest,
    ProviderAuthorizationStartStatus,
    ProviderCompoundCredentialInput,
    ProviderOperationalState,
    ProviderSensitiveInputChallenge,
    SensitiveValue,
)
from app.i18n import LocalizationService
from app.providers.onthespot.process import OnTheSpotProcessClient
from app.providers.onthespot.worker import OnTheSpotWorker
from app.providers.spotify_authorization import (
    SpotifyPlaybackAuthorizationDriver,
    SpotifyPlaybackPairingPoll,
    SpotifyPlaybackPairingStart,
    SpotifyPlaybackPollStatus,
    SpotifyWebApiAuthorizationDriver,
    SpotifyWebApiAuthorizationResult,
)
from app.services.provider_authorization import ProviderAuthorizationCoordinator
from app.telegram.admin_handlers import _parse_spotify_webapi_submission
from app.telegram.provider_accounts_presentation import (
    ProviderAccountsCallbackAction,
    ProviderAccountsPresentation,
    encode_provider_accounts_callback,
    parse_provider_accounts_callback,
)

PLAYBACK_SECRET = "stage134-playback-credential-distinctive"
CLIENT_ID = "stage134-client-id-distinctive"
CLIENT_SECRET = "stage134-client-secret-distinctive"


class FakeConfig:
    def __init__(self, accounts: list[dict[str, Any]] | None = None) -> None:
        self.values: dict[str, Any] = {
            "accounts": list(accounts or []),
            "spotify_webapi_override_client_id": "",
            "spotify_webapi_override_client_secret": "",
        }
        self.save_calls = 0
        self.fail_save = False

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value

    def save(self) -> None:
        self.save_calls += 1
        if self.fail_save:
            raise OSError(CLIENT_SECRET)


class FakeSpotifySession:
    def __init__(self, account_type: str) -> None:
        self.account_type = account_type
        self.closed = False

    def get_user_attribute(self, key: str) -> str:
        assert key == "type"
        return self.account_type

    def close(self) -> None:
        self.closed = True


class FakeZeroconfServer:
    def __init__(self, account_type: str = "premium", *, valid: bool = False) -> None:
        self._ZeroconfServer__session = FakeSpotifySession(account_type)
        self.valid = valid
        self.polls = 0
        self.closed = False

    def has_valid_session(self) -> bool:
        self.polls += 1
        return self.valid

    def close_session(self) -> None:
        self.closed = True
        self._ZeroconfServer__session.close()


def _spotify_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    server: FakeZeroconfServer,
    *,
    accounts: list[dict[str, Any]] | None = None,
) -> tuple[OnTheSpotWorker, FakeConfig]:
    worker = OnTheSpotWorker()
    worker._initialized = True
    worker._config = FakeConfig(accounts)
    worker._runtime = SimpleNamespace(account_pool=[])
    worker._accounts = SimpleNamespace(get_account_token=lambda provider: None)
    monkeypatch.setenv("MUSICBOT_TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("SPOTIFY_CONNECT_HOST_IP", "192.168.50.10")
    monkeypatch.setenv("SPOTIFY_CONNECT_PORT", "24879")
    monkeypatch.setattr(worker_module, "_is_local_ipv4_address", lambda value: True)

    def create(host: str, port: int, path: Path) -> FakeZeroconfServer:
        assert (host, port) == ("192.168.50.10", 24879)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "username": "spotify-user",
                    "credentials": PLAYBACK_SECRET,
                    "type": "AUTHENTICATION_STORED_SPOTIFY_CREDENTIALS",
                }
            ),
            encoding="utf-8",
        )
        return server

    monkeypatch.setattr(worker, "_create_spotify_zeroconf_server", create)
    return worker, worker._config


def test_spotify_connect_config_accepts_only_non_loopback_ipv4() -> None:
    assert Settings(spotify_connect_host_ip="192.168.1.20").spotify_connect_host_ip == (
        "192.168.1.20"
    )
    for value in ("127.0.0.1", "::1", "0.0.0.0", "not-an-ip"):
        with pytest.raises(ValueError):
            Settings(spotify_connect_host_ip=value)


def test_pairing_start_fails_closed_without_reachable_explicit_address(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worker, _ = _spotify_worker(monkeypatch, tmp_path, FakeZeroconfServer())
    monkeypatch.delenv("SPOTIFY_CONNECT_HOST_IP")

    result = worker.spotify_playback_pairing_start()

    assert result == {
        "status": "failed",
        "error_code": "SPOTIFY_PLAYBACK_DISCOVERY_UNAVAILABLE",
    }
    assert worker._spotify_playback_pairing is None


def test_pairing_start_returns_without_wait_loop_and_each_poll_inspects_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    server = FakeZeroconfServer(valid=False)
    worker, _ = _spotify_worker(monkeypatch, tmp_path, server)

    started = worker.spotify_playback_pairing_start()
    pending = worker.spotify_playback_pairing_poll(str(started["flow_id"]))
    status = worker.spotify_component_status()

    assert started["status"] == "started"
    assert started["advertised_host"] == "192.168.50.10:24879"
    assert pending == {
        "status": "pending",
        "error_code": "SPOTIFY_PLAYBACK_PENDING",
    }
    assert server.polls == 1
    assert set(status) == {"playback", "web_api"}
    source = inspect.getsource(OnTheSpotWorker.spotify_playback_pairing_start)
    assert "spotify_new_session" not in source
    assert "while" not in source


def test_success_persists_exact_schema_once_preserves_accounts_and_cleans_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    unrelated = {"uuid": "tidal", "service": "tidal", "active": True, "login": {}}
    server = FakeZeroconfServer(valid=True)
    worker, config = _spotify_worker(monkeypatch, tmp_path, server, accounts=[unrelated])
    started = worker.spotify_playback_pairing_start()
    flow_id = str(started["flow_id"])

    result = worker.spotify_playback_pairing_poll(flow_id)
    duplicate = worker.spotify_playback_pairing_poll(flow_id)

    assert result == {"status": "approved"}
    assert duplicate == result
    assert config.save_calls == 1
    assert config.values["accounts"][0] is unrelated
    account = config.values["accounts"][1]
    assert set(account) == {"uuid", "service", "active", "login"}
    assert account["service"] == "spotify"
    assert account["active"] is True
    assert account["login"] == {
        "username": "spotify-user",
        "credentials": PLAYBACK_SECRET,
        "type": "AUTHENTICATION_STORED_SPOTIFY_CREDENTIALS",
    }
    assert server.closed is True
    assert not (tmp_path / "spotify-pairing" / f"{flow_id}.json").exists()
    assert PLAYBACK_SECRET not in repr(result)

    second_server = FakeZeroconfServer(valid=True)

    def create_again(host: str, port: int, path: Path) -> FakeZeroconfServer:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(account["login"]), encoding="utf-8")
        return second_server

    monkeypatch.setattr(worker, "_create_spotify_zeroconf_server", create_again)
    second = worker.spotify_playback_pairing_start()
    assert worker.spotify_playback_pairing_poll(str(second["flow_id"])) == {"status": "approved"}
    assert len(config.values["accounts"]) == 2
    assert config.save_calls == 1


@pytest.mark.parametrize("mode", ["expiry", "failure"])
def test_pairing_expiry_and_technical_failure_close_discovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    server = FakeZeroconfServer()
    worker, _ = _spotify_worker(monkeypatch, tmp_path, server)
    started = worker.spotify_playback_pairing_start()
    pairing = worker._spotify_playback_pairing
    assert pairing is not None
    if mode == "expiry":
        pairing.expires_at_monotonic = 0
        expected = "SPOTIFY_PLAYBACK_CANCELLED"
    else:
        monkeypatch.setattr(
            server,
            "has_valid_session",
            lambda: (_ for _ in ()).throw(RuntimeError(PLAYBACK_SECRET)),
        )
        expected = "SPOTIFY_PLAYBACK_START_FAILED"

    result = worker.spotify_playback_pairing_poll(str(started["flow_id"]))

    assert result["status"] in {"expired", "failed"}
    assert result["error_code"] == expected
    assert server.closed is True
    assert worker._spotify_playback_pairing is None
    assert PLAYBACK_SECRET not in repr(result)


@pytest.mark.parametrize(
    ("account_type", "code"),
    [
        ("free", "SPOTIFY_PLAYBACK_PREMIUM_REQUIRED"),
        ("unknown", "SPOTIFY_PLAYBACK_UNSUPPORTED_ACCOUNT_TYPE"),
    ],
)
def test_free_and_unknown_playback_accounts_fail_closed_without_persistence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    account_type: str,
    code: str,
) -> None:
    server = FakeZeroconfServer(account_type, valid=True)
    worker, config = _spotify_worker(monkeypatch, tmp_path, server)
    started = worker.spotify_playback_pairing_start()

    result = worker.spotify_playback_pairing_poll(str(started["flow_id"]))

    assert result == {"status": "failed", "error_code": code}
    assert config.values["accounts"] == []
    assert config.save_calls == 0
    assert server.closed is True


def test_pairing_persistence_failure_is_sanitized_and_preserves_previous_accounts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    unrelated = {"service": "deezer"}
    worker, config = _spotify_worker(
        monkeypatch, tmp_path, FakeZeroconfServer(valid=True), accounts=[unrelated]
    )
    config.fail_save = True
    started = worker.spotify_playback_pairing_start()

    result = worker.spotify_playback_pairing_poll(str(started["flow_id"]))

    assert result == {
        "status": "failed",
        "error_code": "SPOTIFY_PLAYBACK_PERSIST_FAILED",
    }
    assert config.values["accounts"] == [unrelated]
    assert PLAYBACK_SECRET not in repr(result)
    assert CLIENT_SECRET not in repr(result)


def test_cancel_is_idempotent_stale_safe_and_shutdown_closes_current_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first_server = FakeZeroconfServer()
    worker, _ = _spotify_worker(monkeypatch, tmp_path, first_server)
    started = worker.spotify_playback_pairing_start()
    flow_id = str(started["flow_id"])

    assert worker.spotify_playback_pairing_cancel("0" * 16) == {"status": "not_found"}
    assert first_server.closed is False
    assert worker.spotify_playback_pairing_cancel(flow_id) == {"status": "cancelled"}
    assert worker.spotify_playback_pairing_cancel(flow_id) == {"status": "released"}
    assert first_server.closed is True

    second_server = FakeZeroconfServer()
    monkeypatch.setattr(
        worker,
        "_create_spotify_zeroconf_server",
        lambda host, port, path: second_server,
    )
    restarted = worker.spotify_playback_pairing_start()
    assert restarted["status"] == "started"
    worker.shutdown()
    assert second_server.closed is True
    assert worker._spotify_playback_pairing is None


class FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self.payload = payload

    def json(self) -> object:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeRequests:
    exceptions = requests.exceptions

    def __init__(self, token: FakeResponse | Exception, probe: FakeResponse | Exception) -> None:
        self.token = token
        self.probe = probe
        self.post_calls: list[tuple[str, dict[str, Any]]] = []
        self.get_calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.post_calls.append((url, kwargs))
        if isinstance(self.token, Exception):
            raise self.token
        return self.token

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.get_calls.append((url, kwargs))
        if isinstance(self.probe, Exception):
            raise self.probe
        return self.probe


def _webapi_worker(
    monkeypatch: pytest.MonkeyPatch,
    token: FakeResponse | Exception,
    probe: FakeResponse | Exception,
) -> tuple[OnTheSpotWorker, FakeConfig, FakeRequests, dict[str, Any]]:
    fake_requests = FakeRequests(token, probe)
    spotify = SimpleNamespace(
        _oauth_token_cache={
            "access_token": "old-cached-token",
            "expires_at": 9999999999,
            "client_id": CLIENT_ID,
        },
        _oauth_token_lock=threading.Lock(),
    )
    real_import = importlib.import_module

    def fake_import(name: str) -> Any:
        if name == "requests":
            return fake_requests
        if name == "onthespot.api.spotify":
            return spotify
        return real_import(name)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    worker = OnTheSpotWorker()
    worker._initialized = True
    worker._config = FakeConfig()
    worker._runtime = SimpleNamespace(account_pool=[])
    worker._accounts = SimpleNamespace(get_account_token=lambda provider: None)
    return worker, worker._config, fake_requests, spotify._oauth_token_cache


def _valid_token() -> FakeResponse:
    return FakeResponse(
        200,
        {"access_token": "child-only-access-token", "token_type": "Bearer", "expires_in": 3600},
    )


def _valid_probe() -> FakeResponse:
    return FakeResponse(200, {"tracks": {"items": [{"name": "not-persisted"}]}})


def test_valid_webapi_pair_uses_https_minimal_probe_persists_keys_and_invalidates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, config, fake_requests, cache = _webapi_worker(
        monkeypatch, _valid_token(), _valid_probe()
    )
    config.values["unrelated_setting"] = "preserved"

    result = worker.spotify_webapi_authorize(CLIENT_ID, CLIENT_SECRET)

    assert result == {"status": "persisted", "operational_state": "AVAILABLE"}
    assert config.values["spotify_webapi_override_client_id"] == CLIENT_ID
    assert config.values["spotify_webapi_override_client_secret"] == CLIENT_SECRET
    assert config.save_calls == 1
    assert config.values["unrelated_setting"] == "preserved"
    assert fake_requests.post_calls[0][0] == "https://accounts.spotify.com/api/token"
    assert fake_requests.post_calls[0][1]["data"] == {"grant_type": "client_credentials"}
    assert fake_requests.get_calls[0][0] == "https://api.spotify.com/v1/search"
    assert fake_requests.get_calls[0][1]["params"] == {
        "q": "test",
        "type": "track",
        "limit": 1,
    }
    assert cache == {"access_token": None, "expires_at": 0, "client_id": None}
    assert "child-only-access-token" not in repr(result)
    assert CLIENT_SECRET not in repr(result)
    assert "not-persisted" not in repr(config.values)


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        (FakeResponse(401, {}), "SPOTIFY_WEBAPI_INVALID_CREDENTIALS"),
        (requests.Timeout(CLIENT_SECRET), "SPOTIFY_WEBAPI_TIMEOUT"),
        (requests.ConnectionError(CLIENT_SECRET), "SPOTIFY_WEBAPI_NETWORK_ERROR"),
        (FakeResponse(200, ValueError(CLIENT_SECRET)), "SPOTIFY_WEBAPI_INVALID_RESPONSE"),
        (FakeResponse(500, {}), "SPOTIFY_WEBAPI_UPSTREAM_ERROR"),
        (FakeResponse(403, {}), "SPOTIFY_WEBAPI_FORBIDDEN"),
        (FakeResponse(429, {}), "SPOTIFY_WEBAPI_RATE_LIMITED"),
    ],
)
def test_token_failures_are_normalized_and_never_persisted(
    monkeypatch: pytest.MonkeyPatch,
    token: FakeResponse | Exception,
    expected: str,
) -> None:
    worker, config, _, _ = _webapi_worker(monkeypatch, token, _valid_probe())

    result = worker.spotify_webapi_authorize(CLIENT_ID, CLIENT_SECRET)

    assert result == {"status": "failed", "error_code": expected}
    assert config.save_calls == 0
    assert config.values["spotify_webapi_override_client_secret"] == ""
    assert CLIENT_SECRET not in repr(result)


@pytest.mark.parametrize(
    ("probe", "operational", "error"),
    [
        (FakeResponse(429, {"error": {}}), "RATE_LIMITED", "SPOTIFY_WEBAPI_RATE_LIMITED"),
        (
            FakeResponse(429, {"error": {"reason": "QUOTA_EXCEEDED"}}),
            "QUOTA_EXCEEDED",
            "SPOTIFY_WEBAPI_QUOTA_EXCEEDED",
        ),
        (FakeResponse(403, {"error": {}}), "FORBIDDEN", "SPOTIFY_WEBAPI_FORBIDDEN"),
    ],
)
def test_probe_restrictions_preserve_valid_credentials_and_readiness(
    monkeypatch: pytest.MonkeyPatch,
    probe: FakeResponse,
    operational: str,
    error: str,
) -> None:
    worker, config, _, _ = _webapi_worker(monkeypatch, _valid_token(), probe)

    result = worker.spotify_webapi_authorize(CLIENT_ID, CLIENT_SECRET)
    status = worker.spotify_component_status()["web_api"]

    assert result == {"status": "persisted", "operational_state": operational}
    assert config.values["spotify_webapi_override_client_secret"] == CLIENT_SECRET
    assert status == {
        "state": "READY",
        "operational_state": operational,
        "error_code": error,
    }


def test_invalid_replacement_and_save_failure_preserve_old_pair_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, config, _, _ = _webapi_worker(monkeypatch, FakeResponse(401, {}), _valid_probe())
    config.values["spotify_webapi_override_client_id"] = "old-client"
    config.values["spotify_webapi_override_client_secret"] = "old-secret"

    rejected = worker.spotify_webapi_authorize(CLIENT_ID, CLIENT_SECRET)
    assert rejected["error_code"] == "SPOTIFY_WEBAPI_INVALID_CREDENTIALS"
    assert config.values["spotify_webapi_override_client_id"] == "old-client"
    assert config.values["spotify_webapi_override_client_secret"] == "old-secret"

    worker, config, _, _ = _webapi_worker(monkeypatch, _valid_token(), _valid_probe())
    config.values["spotify_webapi_override_client_id"] = "old-client"
    config.values["spotify_webapi_override_client_secret"] = "old-secret"
    config.fail_save = True
    failed = worker.spotify_webapi_authorize(CLIENT_ID, CLIENT_SECRET)
    assert failed["error_code"] == "SPOTIFY_WEBAPI_PERSIST_FAILED"
    assert config.values["spotify_webapi_override_client_id"] == "old-client"
    assert config.values["spotify_webapi_override_client_secret"] == "old-secret"


def test_persisted_values_are_revalidated_and_presence_alone_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, config, _, _ = _webapi_worker(monkeypatch, FakeResponse(401, {}), _valid_probe())
    config.values["spotify_webapi_override_client_id"] = CLIENT_ID
    config.values["spotify_webapi_override_client_secret"] = CLIENT_SECRET

    worker._refresh_spotify_webapi_readiness()

    assert worker._spotify_webapi_state == "ERROR"
    assert worker._spotify_webapi_error_code == "SPOTIFY_WEBAPI_INVALID_CREDENTIALS"
    assert config.values["spotify_webapi_override_client_secret"] == CLIENT_SECRET


def test_playback_config_presence_without_runtime_session_is_not_ready() -> None:
    worker = OnTheSpotWorker()
    worker._initialized = True
    worker._config = FakeConfig(
        [
            {
                "uuid": "configured-only",
                "service": "spotify",
                "active": True,
                "login": {
                    "username": "user",
                    "credentials": PLAYBACK_SECRET,
                    "type": "stored",
                },
            }
        ]
    )
    worker._runtime = SimpleNamespace(account_pool=[])
    worker._accounts = SimpleNamespace(get_account_token=lambda provider: None)

    result = worker.spotify_component_status()

    assert result["playback"] == {
        "state": "AUTH_REQUIRED",
        "error_code": "SESSION_UNAVAILABLE",
    }
    assert PLAYBACK_SECRET not in repr(result)


@pytest.mark.parametrize(
    "value",
    ["", "one-line", "a\nb\nc", "\nsecret", "id\n", "id\nsec\x00ret", "id\rsecret"],
)
def test_webapi_parent_parser_rejects_every_non_exact_two_line_format(value: str) -> None:
    assert _parse_spotify_webapi_submission(value) is None


def test_webapi_parent_parser_trims_spaces_only_and_never_echoes_values() -> None:
    assert _parse_spotify_webapi_submission(f"  {CLIENT_ID}  \r\n {CLIENT_SECRET} ") == (
        CLIENT_ID,
        CLIENT_SECRET,
    )


class DriverBackend:
    def __init__(self, playback: ProviderAccountState, web_api: ProviderAccountState) -> None:
        self.playback = playback
        self.web_api = web_api
        self.reload_calls = 0

    async def reload_account_state(self) -> None:
        self.reload_calls += 1

    async def get_account_status(self, provider: MusicProviderName) -> ProviderAccountStatus:
        return ProviderAccountStatus(
            provider,
            self.playback,
            datetime.now(UTC),
            components=(
                ProviderAccountComponentStatus(ProviderAccountComponent.PLAYBACK, self.playback),
                ProviderAccountComponentStatus(
                    ProviderAccountComponent.WEB_API,
                    self.web_api,
                    operational_state=ProviderOperationalState.AVAILABLE,
                ),
            ),
        )

    async def disconnect_account(self, provider: MusicProviderName) -> Any:
        raise AssertionError(provider)


async def test_playback_driver_reloads_then_requires_component_runtime_ready() -> None:
    boundary = SimpleNamespace(
        start_spotify_playback_pairing=AsyncMock(
            return_value=SpotifyPlaybackPairingStart(
                "started", "a" * 16, "192.168.1.5:24879", 30, 0.001
            )
        ),
        poll_spotify_playback_pairing=AsyncMock(
            return_value=SpotifyPlaybackPairingPoll(SpotifyPlaybackPollStatus.APPROVED)
        ),
        cancel_spotify_playback_pairing=AsyncMock(),
    )
    backend = DriverBackend(ProviderAccountState.READY, ProviderAccountState.NOT_CONFIGURED)
    driver = SpotifyPlaybackAuthorizationDriver(boundary, backend)
    challenge = await driver.start(
        ProviderAuthorizationRequest(
            MusicProviderName.SPOTIFY, ProviderAuthorizationMethod.BROWSER_DEVICE_LINK
        )
    )

    outcome = await driver.wait(challenge)

    assert outcome.status is ProviderAuthorizationOutcomeStatus.READY
    assert backend.reload_calls == 1
    boundary.poll_spotify_playback_pairing.assert_awaited_once_with("a" * 16)
    boundary.cancel_spotify_playback_pairing.assert_awaited_once_with("a" * 16)


async def test_playback_reload_failure_never_reports_ready() -> None:
    boundary = SimpleNamespace()
    backend = DriverBackend(ProviderAccountState.ERROR, ProviderAccountState.READY)
    outcome = await SpotifyPlaybackAuthorizationDriver(boundary, backend)._reload_and_verify()
    assert outcome.status is ProviderAuthorizationOutcomeStatus.FAILED
    assert outcome.error_code is ProviderAccountErrorCode.SPOTIFY_PLAYBACK_RELOAD_FAILED


async def test_webapi_driver_uses_component_readiness_without_reloading_playback() -> None:
    boundary = SimpleNamespace(
        authorize_spotify_webapi_credentials=AsyncMock(
            return_value=SpotifyWebApiAuthorizationResult(
                True, ProviderOperationalState.RATE_LIMITED
            )
        )
    )
    backend = DriverBackend(ProviderAccountState.AUTH_REQUIRED, ProviderAccountState.READY)
    outcome = await SpotifyWebApiAuthorizationDriver(boundary, backend).authorize_credentials(
        ProviderCompoundCredentialInput(
            MusicProviderName.SPOTIFY,
            SensitiveValue(CLIENT_ID),
            SensitiveValue(CLIENT_SECRET),
        )
    )
    assert outcome.status is ProviderAuthorizationOutcomeStatus.READY
    assert backend.reload_calls == 0
    assert CLIENT_SECRET not in repr(outcome)


class WaitingCompoundDriver:
    def __init__(self) -> None:
        self.calls = 0

    async def authorize_credentials(self, credentials: ProviderCompoundCredentialInput) -> Any:
        self.calls += 1
        return SimpleNamespace(
            provider=credentials.provider,
            status=ProviderAuthorizationOutcomeStatus.READY,
            error_code=None,
        )


async def test_compound_coordinator_is_generation_and_method_bound() -> None:
    driver = WaitingCompoundDriver()
    request = ProviderAuthorizationRequest(
        MusicProviderName.SPOTIFY, ProviderAuthorizationMethod.COMPOUND_CREDENTIALS
    )
    coordinator = ProviderAuthorizationCoordinator({(request.provider, request.method): driver})
    started = await coordinator.start(request)
    assert started.status is ProviderAuthorizationStartStatus.STARTED
    assert isinstance(started.challenge, ProviderSensitiveInputChallenge)
    assert (
        started.challenge.authorization_method is ProviderAuthorizationMethod.COMPOUND_CREDENTIALS
    )
    assert (
        await coordinator.pending_sensitive_challenge(
            request.provider, ProviderAuthorizationMethod.SENSITIVE_SECRET
        )
        is None
    )

    stale = await coordinator.submit_compound_credentials(
        request.provider,
        "0" * 16,
        ProviderCompoundCredentialInput(
            request.provider, SensitiveValue(CLIENT_ID), SensitiveValue(CLIENT_SECRET)
        ),
    )
    assert stale.error_code is ProviderAccountErrorCode.AUTHORIZATION_STALE_FLOW
    assert driver.calls == 0
    await coordinator.cancel(request.provider, started.challenge.flow_id)
    await coordinator.close()


async def test_process_webapi_boundary_never_returns_token_or_secret() -> None:
    client = OnTheSpotProcessClient()
    client._request = AsyncMock(  # type: ignore[method-assign]
        return_value={"status": "persisted", "operational_state": "AVAILABLE"}
    )
    result = await client.authorize_spotify_webapi_credentials(
        SensitiveValue(CLIENT_ID), SensitiveValue(CLIENT_SECRET)
    )
    assert result.persisted is True
    assert CLIENT_ID not in repr(result)
    assert CLIENT_SECRET not in repr(result)
    assert "access_token" not in repr(result)


def test_spotify_component_ui_and_distinct_strict_callbacks_are_secret_free() -> None:
    presentation = ProviderAccountsPresentation(LocalizationService(("en", "ru"), "en"))
    status = ProviderAccountStatus(
        MusicProviderName.SPOTIFY,
        ProviderAccountState.AUTH_REQUIRED,
        datetime.now(UTC),
        (
            ProviderAuthorizationMethod.BROWSER_DEVICE_LINK,
            ProviderAuthorizationMethod.COMPOUND_CREDENTIALS,
        ),
        components=(
            ProviderAccountComponentStatus(
                ProviderAccountComponent.PLAYBACK, ProviderAccountState.AUTH_REQUIRED
            ),
            ProviderAccountComponentStatus(
                ProviderAccountComponent.WEB_API,
                ProviderAccountState.NOT_CONFIGURED,
                operational_state=ProviderOperationalState.UNKNOWN,
            ),
        ),
    )
    text = presentation.detail_text(status, "en")
    buttons = [
        button
        for row in presentation.detail_keyboard(status, "en").inline_keyboard
        for button in row
    ]
    callbacks = [button.callback_data for button in buttons if button.callback_data]
    assert "Playback: Authorization required" in text
    assert "Search / Web API: Not configured" in text
    assert "Connect Playback" in [button.text for button in buttons]
    assert "Configure Search API" in [button.text for button in buttons]
    assert any(value == "adm5:p:spotify" for value in callbacks)
    assert any(value == "adm5:w:spotify" for value in callbacks)
    assert all(CLIENT_SECRET not in value and PLAYBACK_SECRET not in value for value in callbacks)
    for value in callbacks:
        assert parse_provider_accounts_callback(value) is not None
        assert len(value.encode()) <= 64
    assert parse_provider_accounts_callback("adm5:p:tidal:extra") is None
    assert parse_provider_accounts_callback("adm5:w:spotify:" + CLIENT_ID) is None
    with pytest.raises(ValueError):
        encode_provider_accounts_callback(
            ProviderAccountsCallbackAction.CANCEL,
            MusicProviderName.SPOTIFY,
            CLIENT_SECRET,
        )


def test_stage134_does_not_introduce_user_oauth_search_ui_or_mirror_polling() -> None:
    import app.composition as composition

    source = inspect.getsource(composition)
    worker_source = inspect.getsource(worker_module)
    assert "redirect_uri" not in worker_source
    assert "Authorization Code" not in worker_source
    assert "MirrorSpotifyPlayback" not in source
    assert "currently-playing" not in source
    assert "spotify_new_session" not in worker_source


def test_secret_markers_never_escape_sanitized_logs_or_dtos(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    values = (
        SensitiveValue(PLAYBACK_SECRET),
        SensitiveValue(CLIENT_SECRET),
        ProviderCompoundCredentialInput(
            MusicProviderName.SPOTIFY,
            SensitiveValue(CLIENT_ID),
            SensitiveValue(CLIENT_SECRET),
        ),
    )
    rendered = repr(values) + str(values) + caplog.text
    assert PLAYBACK_SECRET not in rendered
    assert CLIENT_SECRET not in rendered
