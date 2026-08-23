from __future__ import annotations

import asyncio
import importlib
import json
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import requests

from app.core.enums import MusicProviderName
from app.core.provider_accounts import (
    ProviderAccountErrorCode,
    ProviderAccountState,
    ProviderAccountStatus,
    ProviderAuthorizationChallenge,
    ProviderAuthorizationMethod,
    ProviderAuthorizationOutcome,
    ProviderAuthorizationOutcomeStatus,
    ProviderAuthorizationRequest,
    ProviderAuthorizationStartStatus,
    ProviderSecretInput,
    ProviderSensitiveInputChallenge,
    SensitiveValue,
)
from app.i18n import LocalizationService
from app.providers.deezer_authorization import (
    DeezerArlAuthorizationDriver,
    DeezerArlAuthorizationResult,
)
from app.providers.onthespot.process import OnTheSpotProcessClient
from app.providers.onthespot.worker import OnTheSpotWorker
from app.services.provider_authorization import ProviderAuthorizationCoordinator
from app.telegram.provider_accounts_presentation import ProviderAccountsPresentation

SECRET = "stage133-distinctive-test-secret-ARL_1234567890"


class FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self.response = response
        self.headers: dict[str, str] = {}
        self.cookies: dict[str, str] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def close(self) -> None:
        self.closed = True


class FakeRequests:
    exceptions = requests.exceptions

    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = responses
        self.sessions: list[FakeSession] = []

    def Session(self) -> FakeSession:  # noqa: N802 - requests API compatibility
        session = FakeSession(self.responses.pop(0))
        self.sessions.append(session)
        return session


class FakeConfig:
    def __init__(self, accounts: list[dict[str, Any]] | None = None) -> None:
        self.values: dict[str, Any] = {"accounts": list(accounts or [])}
        self.save_calls = 0
        self.fail_save = False

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value

    def save(self) -> None:
        self.save_calls += 1
        if self.fail_save:
            raise OSError(f"arl={SECRET}")


def _valid_payload() -> dict[str, Any]:
    return {
        "results": {
            "checkForm": "child-only-check-form",
            "USER": {
                "USER_ID": 1234,
                "OPTIONS": {
                    "web_lossless": True,
                    "web_hq": True,
                    "license_token": "child-only-license-token",
                },
            },
        }
    }


def _anonymous_payload() -> dict[str, Any]:
    return {
        "results": {
            "checkForm": "anonymous-check-form",
            "USER": {
                "USER_ID": 0,
                "OPTIONS": {"license_token": "anonymous-license"},
            },
        }
    }


def _worker_with_deezer(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[FakeResponse | Exception],
    *,
    accounts: list[dict[str, Any]] | None = None,
) -> tuple[OnTheSpotWorker, FakeRequests, FakeConfig]:
    fake_requests = FakeRequests(responses)
    deezer = SimpleNamespace(requests=fake_requests)
    real_import = importlib.import_module

    def fake_import(name: str) -> Any:
        if name == "onthespot.api.deezer":
            return deezer
        return real_import(name)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    worker = OnTheSpotWorker()
    worker._initialized = True
    worker._config = FakeConfig(accounts)
    worker._runtime = SimpleNamespace(account_pool=[])
    worker._registry = SimpleNamespace(SERVICE_LOGIN_FUNCTIONS={})
    return worker, fake_requests, worker._config


@pytest.mark.parametrize(
    "value",
    ["", "   ", "short", "line-one\nline-two", "control\x00value", "x" * 2049, "bad;cookie"],
)
def test_child_rejects_implausible_arl_before_network_or_persistence(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    worker, fake_requests, config = _worker_with_deezer(monkeypatch, [])

    result = worker.deezer_arl_authorize(value)

    assert result == {
        "status": "failed",
        "error_code": "DEEZER_ARL_INVALID_FORMAT",
    }
    assert fake_requests.sessions == []
    assert config.values["accounts"] == []
    assert config.save_calls == 0
    assert value not in repr(result) or not value


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (FakeResponse(401, {}), "DEEZER_ARL_INVALID"),
        (FakeResponse(403, {}), "DEEZER_ARL_INVALID"),
        (FakeResponse(500, {}), "DEEZER_AUTH_UPSTREAM_ERROR"),
        (FakeResponse(200, ValueError(SECRET)), "DEEZER_AUTH_INVALID_RESPONSE"),
        (FakeResponse(200, {"unexpected": True}), "DEEZER_AUTH_INVALID_RESPONSE"),
        (FakeResponse(200, _anonymous_payload()), "DEEZER_ARL_INVALID"),
        (requests.Timeout(SECRET), "DEEZER_AUTH_TIMEOUT"),
        (requests.ConnectionError(SECRET), "DEEZER_AUTH_NETWORK_ERROR"),
    ],
)
def test_child_normalizes_deezer_failures_without_persisting_or_leaking(
    monkeypatch: pytest.MonkeyPatch,
    response: FakeResponse | Exception,
    expected: str,
) -> None:
    worker, fake_requests, config = _worker_with_deezer(monkeypatch, [response])

    result = worker.deezer_arl_authorize(SECRET)

    assert result == {"status": "failed", "error_code": expected}
    assert config.values["accounts"] == []
    assert config.save_calls == 0
    assert SECRET not in repr(result)
    assert fake_requests.sessions[0].closed is True


def test_valid_arl_uses_https_persists_exact_schema_and_preserves_unrelated_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unrelated = {"uuid": "tidal-existing", "service": "tidal", "active": True, "login": {}}
    old_deezer = {
        "uuid": "deezer-old",
        "service": "deezer",
        "active": True,
        "login": {"arl": "different-old-test-credential"},
    }
    worker, fake_requests, config = _worker_with_deezer(
        monkeypatch,
        [FakeResponse(200, _valid_payload())],
        accounts=[unrelated, old_deezer],
    )

    result = worker.deezer_arl_authorize(f"  {SECRET}  ")

    assert result == {"status": "persisted"}
    assert config.save_calls == 1
    assert config.values["accounts"][0] is unrelated
    assert config.values["accounts"][1] is old_deezer
    account = config.values["accounts"][2]
    assert set(account) == {"uuid", "service", "active", "login"}
    assert account["service"] == "deezer"
    assert account["active"] is True
    assert account["login"] == {"arl": SECRET}
    assert set(result) == {"status"}
    assert SECRET not in repr(result)
    session = fake_requests.sessions[0]
    assert session.calls == [
        (
            "https://www.deezer.com/ajax/gw-light.php",
            {
                "params": {
                    "api_version": "1.0",
                    "api_token": "null",
                    "input": "3",
                    "method": "deezer.getUserData",
                },
                "timeout": (5.0, 10.0),
            },
        )
    ]
    assert all(not url.startswith("http://") for url, _ in session.calls)


def test_duplicate_arl_is_validated_but_not_appended_or_saved_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, _, config = _worker_with_deezer(
        monkeypatch,
        [FakeResponse(200, _valid_payload()), FakeResponse(200, _valid_payload())],
    )

    assert worker.deezer_arl_authorize(SECRET) == {"status": "persisted"}
    assert worker.deezer_arl_authorize(SECRET) == {"status": "persisted"}

    deezer_accounts = [
        account for account in config.values["accounts"] if account["service"] == "deezer"
    ]
    assert len(deezer_accounts) == 1
    assert config.save_calls == 1


def test_persistence_failure_restores_in_memory_accounts_and_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unrelated = {"service": "existing"}
    worker, _, config = _worker_with_deezer(
        monkeypatch, [FakeResponse(200, _valid_payload())], accounts=[unrelated]
    )
    config.fail_save = True

    result = worker.deezer_arl_authorize(SECRET)

    assert result == {
        "status": "failed",
        "error_code": "DEEZER_AUTH_PERSIST_FAILED",
    }
    assert config.values["accounts"] == [unrelated]
    assert SECRET not in repr(result)


def test_secure_runtime_login_never_uses_arl_as_username_and_health_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, fake_requests, _ = _worker_with_deezer(
        monkeypatch, [FakeResponse(200, _valid_payload())]
    )
    account = {
        "uuid": "deezer-test",
        "service": "deezer",
        "active": True,
        "login": {"arl": SECRET},
    }

    assert worker._secure_deezer_login(account) is True
    runtime = worker._runtime.account_pool[0]
    assert runtime["username"] == ""
    assert runtime["username"] != SECRET
    assert runtime["status"] == "active"
    worker._accounts = SimpleNamespace(get_account_token=lambda provider: runtime["login"])
    health = worker.check_provider_health("deezer")
    assert health == {
        "status": "READY",
        "requires_authentication": True,
        "download_supported": True,
    }
    assert SECRET not in repr(health)
    assert fake_requests.sessions[0].calls[0][0].startswith("https://")
    assert not fake_requests.sessions[0].calls[0][0].startswith("http://")


def test_persisted_arl_can_reload_through_secure_adapter_and_remains_on_reload_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, fake_requests, config = _worker_with_deezer(
        monkeypatch,
        [FakeResponse(200, _valid_payload()), FakeResponse(500, {})],
    )
    assert worker.deezer_arl_authorize(SECRET) == {"status": "persisted"}
    persisted = list(config.values["accounts"])

    worker._install_secure_deezer_login_adapter()
    login = worker._registry.SERVICE_LOGIN_FUNCTIONS["deezer"]
    assert login(persisted[0]) is False

    assert config.values["accounts"] == persisted
    assert worker._runtime.account_pool[0]["status"] == "error"
    assert worker._runtime.account_pool[0]["username"] == ""
    assert SECRET not in repr(worker._runtime.account_pool[0])
    assert [session.calls[0][0] for session in fake_requests.sessions] == [
        "https://www.deezer.com/ajax/gw-light.php",
        "https://www.deezer.com/ajax/gw-light.php",
    ]


class DriverBackend:
    def __init__(self, state: ProviderAccountState = ProviderAccountState.READY) -> None:
        self.state = state
        self.events: list[str] = []
        self.reload_error: Exception | None = None

    async def reload_account_state(self) -> None:
        self.events.append("reload")
        if self.reload_error is not None:
            raise self.reload_error

    async def get_account_status(self, provider: MusicProviderName) -> ProviderAccountStatus:
        self.events.append("health")
        return ProviderAccountStatus(provider, self.state, datetime.now(UTC))

    async def disconnect_account(self, provider: MusicProviderName) -> Any:
        raise AssertionError(provider)


class DriverBoundary:
    def __init__(self, result: DeezerArlAuthorizationResult) -> None:
        self.result = result
        self.events: list[str] = []

    async def authorize_deezer_arl(
        self, credential: SensitiveValue
    ) -> DeezerArlAuthorizationResult:
        assert str(credential) == "[REDACTED]"
        self.events.append("persist")
        return self.result


async def test_driver_persists_then_reloads_then_requires_runtime_ready() -> None:
    boundary = DriverBoundary(DeezerArlAuthorizationResult(True))
    backend = DriverBackend()
    driver = DeezerArlAuthorizationDriver(boundary, backend)

    outcome = await driver.authorize_secret(
        ProviderSecretInput(MusicProviderName.DEEZER, SensitiveValue(SECRET))
    )

    assert outcome.status is ProviderAuthorizationOutcomeStatus.READY
    assert boundary.events + backend.events == ["persist", "reload", "health"]
    assert SECRET not in repr(outcome)

    backend = DriverBackend(ProviderAccountState.AUTH_REQUIRED)
    failed = await DeezerArlAuthorizationDriver(boundary, backend).authorize_secret(
        ProviderSecretInput(MusicProviderName.DEEZER, SensitiveValue(SECRET))
    )
    assert failed.status is ProviderAuthorizationOutcomeStatus.FAILED
    assert failed.error_code is ProviderAccountErrorCode.DEEZER_AUTH_RELOAD_FAILED


async def test_reload_failure_does_not_report_ready_or_undo_child_persistence() -> None:
    boundary = DriverBoundary(DeezerArlAuthorizationResult(True))
    backend = DriverBackend()
    backend.reload_error = RuntimeError(SECRET)

    outcome = await DeezerArlAuthorizationDriver(boundary, backend).authorize_secret(
        ProviderSecretInput(MusicProviderName.DEEZER, SensitiveValue(SECRET))
    )

    assert boundary.events == ["persist"]
    assert outcome.status is ProviderAuthorizationOutcomeStatus.FAILED
    assert outcome.error_code is ProviderAccountErrorCode.DEEZER_AUTH_RELOAD_FAILED
    assert SECRET not in repr(outcome)


class WaitingSecretDriver:
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def authorize_secret(
        self, credential: ProviderSecretInput
    ) -> ProviderAuthorizationOutcome:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return ProviderAuthorizationOutcome(
            credential.provider, ProviderAuthorizationOutcomeStatus.READY
        )


async def test_sensitive_coordinator_generation_conflict_cancel_stale_and_cleanup() -> None:
    driver = WaitingSecretDriver()
    request = ProviderAuthorizationRequest(
        MusicProviderName.DEEZER, ProviderAuthorizationMethod.SENSITIVE_SECRET
    )
    coordinator = ProviderAuthorizationCoordinator({(request.provider, request.method): driver})

    started = await coordinator.start(request)
    assert started.status is ProviderAuthorizationStartStatus.STARTED
    assert isinstance(started.challenge, ProviderSensitiveInputChallenge)
    assert driver.calls == 0
    conflict = await coordinator.start(request)
    assert conflict.status is ProviderAuthorizationStartStatus.ALREADY_ACTIVE
    stale = await coordinator.submit_sensitive_secret(
        request.provider,
        "0" * 16,
        ProviderSecretInput(request.provider, SensitiveValue(SECRET)),
    )
    assert stale.error_code is ProviderAccountErrorCode.AUTHORIZATION_STALE_FLOW
    assert driver.calls == 0
    cancelled = await coordinator.cancel(request.provider, started.challenge.flow_id)
    assert cancelled.status is ProviderAuthorizationOutcomeStatus.CANCELLED
    assert driver.calls == 0

    restarted = await coordinator.start(request)
    assert isinstance(restarted.challenge, ProviderSensitiveInputChallenge)
    submission = asyncio.create_task(
        coordinator.submit_sensitive_secret(
            request.provider,
            restarted.challenge.flow_id,
            ProviderSecretInput(request.provider, SensitiveValue(SECRET)),
        )
    )
    await driver.started.wait()
    during_submit = await coordinator.cancel(request.provider, restarted.challenge.flow_id)
    assert during_submit.status is ProviderAuthorizationOutcomeStatus.ALREADY_ACTIVE
    driver.release.set()
    assert (await submission).status is ProviderAuthorizationOutcomeStatus.READY
    await asyncio.sleep(0)
    assert not await coordinator.is_active(request.provider)
    await coordinator.close()


async def test_shutdown_cancels_waiting_sensitive_flow_without_provider_request() -> None:
    driver = WaitingSecretDriver()
    request = ProviderAuthorizationRequest(
        MusicProviderName.DEEZER, ProviderAuthorizationMethod.SENSITIVE_SECRET
    )
    coordinator = ProviderAuthorizationCoordinator({(request.provider, request.method): driver})
    started = await coordinator.start(request)
    assert isinstance(started.challenge, ProviderSensitiveInputChallenge)
    waiting = asyncio.create_task(coordinator.wait(request.provider, started.challenge.flow_id))

    await coordinator.close()

    assert (await waiting).status is ProviderAuthorizationOutcomeStatus.CANCELLED
    assert driver.calls == 0
    assert not await coordinator.is_active(request.provider)


async def test_process_boundary_returns_only_allowlisted_sanitized_result() -> None:
    client = OnTheSpotProcessClient()
    client._request = AsyncMock(return_value={"status": "persisted"})  # type: ignore[method-assign]

    result = await client.authorize_deezer_arl(SensitiveValue(SECRET))

    assert result == DeezerArlAuthorizationResult(True)
    assert SECRET not in repr(result)
    with pytest.raises(TypeError):
        json.dumps(SensitiveValue(SECRET))


async def test_failures_after_secret_receipt_do_not_escape_into_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class ExplodingBoundary:
        async def authorize_deezer_arl(self, credential: SensitiveValue) -> Any:
            raise RuntimeError(credential.reveal_to_provider_backend())

    caplog.set_level(logging.DEBUG)
    outcome = await DeezerArlAuthorizationDriver(
        ExplodingBoundary(), DriverBackend()
    ).authorize_secret(ProviderSecretInput(MusicProviderName.DEEZER, SensitiveValue(SECRET)))

    assert outcome.status is ProviderAuthorizationOutcomeStatus.FAILED
    assert SECRET not in caplog.text
    assert SECRET not in repr(outcome)
    assert SECRET not in str(outcome)


def test_deezer_presentation_is_secret_free_and_spotify_remains_unsupported() -> None:
    presentation = ProviderAccountsPresentation(LocalizationService(("en", "ru"), "en"))
    deezer = ProviderAccountStatus(
        MusicProviderName.DEEZER,
        ProviderAccountState.AUTH_REQUIRED,
        datetime.now(UTC),
        (ProviderAuthorizationMethod.SENSITIVE_SECRET,),
    )
    spotify = ProviderAccountStatus(
        MusicProviderName.SPOTIFY,
        ProviderAccountState.AUTH_REQUIRED,
        datetime.now(UTC),
    )
    tidal = ProviderAccountStatus(
        MusicProviderName.TIDAL,
        ProviderAccountState.AUTH_REQUIRED,
        datetime.now(UTC),
        (ProviderAuthorizationMethod.BROWSER_DEVICE_LINK,),
    )
    sensitive = ProviderSensitiveInputChallenge(MusicProviderName.DEEZER, "a" * 16)
    browser = ProviderAuthorizationChallenge(
        MusicProviderName.TIDAL,
        "b" * 16,
        "https://login.tidal.com/device",
        datetime.now(UTC),
        1,
    )

    deezer_buttons = [
        button
        for row in presentation.detail_keyboard(deezer, "en").inline_keyboard
        for button in row
    ]
    spotify_buttons = [
        button
        for row in presentation.detail_keyboard(spotify, "en").inline_keyboard
        for button in row
    ]
    tidal_buttons = [
        button
        for row in presentation.detail_keyboard(tidal, "en").inline_keyboard
        for button in row
    ]
    sensitive_buttons = presentation.authorization_keyboard(sensitive, "en").inline_keyboard
    browser_buttons = presentation.authorization_keyboard(browser, "en").inline_keyboard

    assert any(button.text == "Connect Deezer" for button in deezer_buttons)
    assert not any("Connect" in button.text for button in spotify_buttons)
    assert any(button.text == "Connect Tidal" for button in tidal_buttons)
    assert sensitive_buttons[0][0].url is None
    assert sensitive_buttons[0][0].callback_data == "adm5:x:deezer:" + "a" * 16
    assert browser_buttons[0][0].url == "https://login.tidal.com/device"
    rendered = repr(
        (
            presentation.detail_text(deezer, "en"),
            presentation.authorization_text(sensitive, "en"),
            sensitive_buttons,
        )
    )
    assert SECRET not in rendered
    assert "abcdef" not in rendered
