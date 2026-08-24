from __future__ import annotations

import asyncio
import importlib
import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

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
)
from app.i18n import LocalizationService
from app.providers.onthespot.worker import OnTheSpotWorker
from app.providers.tidal_authorization import (
    TidalDeviceAuthorizationDriver,
    TidalDeviceAuthorizationPoll,
    TidalDeviceAuthorizationStart,
    TidalDevicePollStatus,
)
from app.services.provider_authorization import ProviderAuthorizationCoordinator
from app.telegram.provider_accounts_presentation import (
    ProviderAccountsCallbackAction,
    ProviderAccountsPresentation,
    encode_provider_accounts_callback,
    parse_provider_accounts_callback,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeRequests:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeConfig:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {"accounts": [{"service": "existing"}]}
        self.save_calls = 0

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value

    def save(self) -> None:
        self.save_calls += 1


def _device_response(*, interval: int = 1, expires_in: int = 300) -> FakeResponse:
    return FakeResponse(
        200,
        {
            "deviceCode": "child-only-device-code",
            "verificationUriComplete": "https://login.tidal.com/device?user_code=SAFE",
            "expiresIn": expires_in,
            "interval": interval,
        },
    )


def _token_response() -> FakeResponse:
    return FakeResponse(
        200,
        {
            "access_token": "child-only-access-token",
            "refresh_token": "child-only-refresh-token",
            "expires_in": 3600,
            "user": {"username": "safe-user", "countryCode": "US"},
        },
    )


def _worker_with_tidal(
    monkeypatch: pytest.MonkeyPatch, responses: list[FakeResponse | Exception]
) -> tuple[OnTheSpotWorker, FakeRequests, FakeConfig]:
    requests = FakeRequests(responses)
    tidal = SimpleNamespace(
        requests=requests,
        AUTH_URL="https://auth.tidal.com/v1/oauth2",
        CLIENT_ID="child-client-id",
        AUTH=("child-client-id", "child-client-secret"),
    )
    real_import = importlib.import_module

    def fake_import(name: str) -> Any:
        if name == "onthespot.api.tidal":
            return tidal
        return real_import(name)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    worker = OnTheSpotWorker()
    worker._initialized = True
    config = FakeConfig()
    worker._config = config
    worker._runtime = SimpleNamespace(account_pool=[])
    return worker, requests, config


def _start_and_make_poll_due(worker: OnTheSpotWorker) -> tuple[str, dict[str, Any]]:
    started = worker.tidal_device_authorization_start()
    flow_id = started["flow_id"]
    worker._tidal_device_flows[flow_id].next_poll_at_monotonic = 0
    return flow_id, started


def test_child_start_returns_only_sanitized_challenge(monkeypatch: pytest.MonkeyPatch) -> None:
    worker, requests, _ = _worker_with_tidal(monkeypatch, [_device_response()])
    _, result = _start_and_make_poll_due(worker)

    assert set(result) == {"status", "flow_id", "verification_url", "expires_in", "interval"}
    serialized = repr(result)
    assert "child-only-device-code" not in serialized
    assert "child-client-secret" not in serialized
    assert requests.calls[0]["timeout"] == (5.0, 10.0)
    assert "child-only-device-code" not in repr(worker._tidal_device_flows)


def test_child_poll_does_not_call_or_emulate_upstream_blocking_loop() -> None:
    source = inspect.getsource(OnTheSpotWorker.tidal_device_authorization_poll)
    assert "tidal_add_account_pt2" not in source
    assert "while " not in source


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (FakeResponse(400, {"error": "authorization_pending"}), "pending"),
        (FakeResponse(429, {"error": "slow_down"}), "slow_down"),
        (FakeResponse(400, {"error": "access_denied"}), "denied"),
        (FakeResponse(400, {"error": "expired_token"}), "expired"),
        (FakeResponse(400, {"unexpected": True}), "invalid_response"),
        (FakeResponse(200, ValueError("malformed body")), "invalid_response"),
    ],
)
def test_child_poll_normalizes_protocol_outcomes_and_makes_one_request(
    monkeypatch: pytest.MonkeyPatch, response: FakeResponse, expected: str
) -> None:
    worker, requests, _ = _worker_with_tidal(monkeypatch, [_device_response(), response])
    flow_id, _ = _start_and_make_poll_due(worker)

    result = worker.tidal_device_authorization_poll(flow_id)

    assert result["status"] == expected
    assert len(requests.calls) == 2
    assert requests.calls[-1]["timeout"] == (5.0, 10.0)


def test_child_poll_network_failure_is_sanitized_and_retriable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, requests, _ = _worker_with_tidal(
        monkeypatch, [_device_response(), OSError("access_token=raw")]
    )
    flow_id, _ = _start_and_make_poll_due(worker)

    result = worker.tidal_device_authorization_poll(flow_id)

    assert result["status"] == "network_error"
    assert result["error_code"] == "TIDAL_AUTH_NETWORK_ERROR"
    assert "raw" not in repr(result)
    assert len(requests.calls) == 2


def test_child_success_persists_upstream_schema_exactly_once_without_token_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, requests, config = _worker_with_tidal(
        monkeypatch, [_device_response(), _token_response()]
    )
    flow_id, _ = _start_and_make_poll_due(worker)

    first = worker.tidal_device_authorization_poll(flow_id)
    second = worker.tidal_device_authorization_poll(flow_id)

    assert first == second == {"status": "approved"}
    assert config.save_calls == 1
    assert len(config.values["accounts"]) == 2
    assert config.values["accounts"][0] == {"service": "existing"}
    account = config.values["accounts"][1]
    assert set(account) == {"uuid", "service", "active", "login"}
    assert set(account["login"]) == {
        "username",
        "country_code",
        "access_token",
        "refresh_token",
        "token_expiry",
    }
    assert "child-only-access-token" not in repr(first)
    assert "child-only-refresh-token" not in repr(first)
    assert len(requests.calls) == 2


def test_reauthorization_replaces_same_tidal_identity_without_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, _, config = _worker_with_tidal(monkeypatch, [_device_response(), _token_response()])
    config.values["accounts"] = [
        {"service": "existing"},
        {
            "uuid": "stable-tidal-uuid",
            "service": "tidal",
            "active": True,
            "login": {
                "username": "safe-user",
                "country_code": "US",
                "access_token": "old",
                "refresh_token": "old",
                "token_expiry": 1,
            },
        },
    ]
    flow_id, _ = _start_and_make_poll_due(worker)

    assert worker.tidal_device_authorization_poll(flow_id) == {"status": "approved"}
    tidal_accounts = [
        account for account in config.values["accounts"] if account.get("service") == "tidal"
    ]
    assert len(tidal_accounts) == 1
    assert tidal_accounts[0]["uuid"] == "stable-tidal-uuid"
    assert tidal_accounts[0]["login"]["refresh_token"] == "child-only-refresh-token"
    assert config.save_calls == 1


def test_child_persistence_failure_rolls_back_in_memory_and_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, _, config = _worker_with_tidal(monkeypatch, [_device_response(), _token_response()])

    def fail_save() -> None:
        raise OSError("refresh_token=must-not-escape")

    config.save = fail_save  # type: ignore[method-assign]
    flow_id, _ = _start_and_make_poll_due(worker)
    result = worker.tidal_device_authorization_poll(flow_id)

    assert result == {"status": "persist_failed", "error_code": "TIDAL_AUTH_PERSIST_FAILED"}
    assert config.values["accounts"] == [{"service": "existing"}]
    assert "must-not-escape" not in repr(result)


def test_child_cancel_is_idempotent_and_never_removes_persisted_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, _, config = _worker_with_tidal(monkeypatch, [_device_response(), _token_response()])
    flow_id, _ = _start_and_make_poll_due(worker)
    assert worker.tidal_device_authorization_poll(flow_id) == {"status": "approved"}

    assert worker.tidal_device_authorization_cancel(flow_id) == {"status": "released"}
    assert worker.tidal_device_authorization_cancel(flow_id) == {"status": "not_found"}
    assert len(config.values["accounts"]) == 2


class Boundary:
    def __init__(self, polls: list[TidalDeviceAuthorizationPoll]) -> None:
        self.polls = polls
        self.poll_calls = 0
        self.cancel_calls: list[str] = []

    async def start_tidal_device_authorization(self) -> TidalDeviceAuthorizationStart:
        return TidalDeviceAuthorizationStart(
            "started", "a" * 16, "https://login.tidal.com/device", 2, 0.001
        )

    async def poll_tidal_device_authorization(self, flow_id: str) -> TidalDeviceAuthorizationPoll:
        assert flow_id == "a" * 16
        self.poll_calls += 1
        return self.polls.pop(0)

    async def cancel_tidal_device_authorization(self, flow_id: str) -> None:
        self.cancel_calls.append(flow_id)


class Backend:
    def __init__(self, state: ProviderAccountState = ProviderAccountState.READY) -> None:
        self.state = state
        self.reload_calls = 0

    async def reload_account_state(self) -> None:
        self.reload_calls += 1

    async def get_account_status(self, provider: MusicProviderName) -> ProviderAccountStatus:
        return ProviderAccountStatus(provider, self.state, datetime.now(UTC))

    async def disconnect_account(self, provider: MusicProviderName) -> Any:
        raise AssertionError(provider)


async def test_driver_polls_one_operation_at_a_time_then_reloads_and_verifies_ready() -> None:
    boundary = Boundary(
        [
            TidalDeviceAuthorizationPoll(TidalDevicePollStatus.PENDING, 0.001),
            TidalDeviceAuthorizationPoll(TidalDevicePollStatus.SLOW_DOWN, 0.001),
            TidalDeviceAuthorizationPoll(TidalDevicePollStatus.APPROVED),
        ]
    )
    backend = Backend()
    driver = TidalDeviceAuthorizationDriver(boundary, backend)
    request = ProviderAuthorizationRequest(
        MusicProviderName.TIDAL, ProviderAuthorizationMethod.BROWSER_DEVICE_LINK
    )

    challenge = await driver.start(request)
    outcome = await driver.wait(challenge)

    assert outcome.status is ProviderAuthorizationOutcomeStatus.READY
    assert boundary.poll_calls == 3
    assert backend.reload_calls == 1
    assert boundary.cancel_calls == ["a" * 16]


async def test_driver_never_reports_ready_when_runtime_verification_fails() -> None:
    boundary = Boundary([TidalDeviceAuthorizationPoll(TidalDevicePollStatus.APPROVED)])
    backend = Backend(ProviderAccountState.AUTH_REQUIRED)
    driver = TidalDeviceAuthorizationDriver(boundary, backend)
    challenge = await driver.start(
        ProviderAuthorizationRequest(
            MusicProviderName.TIDAL, ProviderAuthorizationMethod.BROWSER_DEVICE_LINK
        )
    )

    outcome = await driver.wait(challenge)

    assert outcome.status is ProviderAuthorizationOutcomeStatus.FAILED
    assert outcome.error_code is ProviderAccountErrorCode.TIDAL_AUTH_RELOAD_FAILED
    assert backend.reload_calls == 1


class BlockingBrowserDriver:
    def __init__(self) -> None:
        self.generation = 0
        self.events: dict[str, asyncio.Event] = {}
        self.cancelled: list[str] = []

    async def start(self, request: ProviderAuthorizationRequest) -> ProviderAuthorizationChallenge:
        self.generation += 1
        flow_id = f"{self.generation:016x}"
        self.events[flow_id] = asyncio.Event()
        return ProviderAuthorizationChallenge(
            request.provider,
            flow_id,
            "https://login.tidal.com/device",
            datetime.now(UTC) + timedelta(minutes=1),
            1,
        )

    async def wait(self, challenge: ProviderAuthorizationChallenge) -> ProviderAuthorizationOutcome:
        await self.events[challenge.flow_id].wait()
        return ProviderAuthorizationOutcome(
            challenge.provider, ProviderAuthorizationOutcomeStatus.READY
        )

    async def cancel(self, flow_id: str) -> None:
        self.cancelled.append(flow_id)


async def test_coordinator_conflict_cancel_idempotency_and_stale_generation_safety() -> None:
    driver = BlockingBrowserDriver()
    request = ProviderAuthorizationRequest(
        MusicProviderName.TIDAL, ProviderAuthorizationMethod.BROWSER_DEVICE_LINK
    )
    coordinator = ProviderAuthorizationCoordinator({(request.provider, request.method): driver})

    first = await coordinator.start(request)
    conflict = await coordinator.start(request)
    assert first.status is ProviderAuthorizationStartStatus.STARTED
    assert conflict.status is ProviderAuthorizationStartStatus.ALREADY_ACTIVE
    assert first.challenge is not None
    old_flow = first.challenge.flow_id
    cancelled = await coordinator.cancel(request.provider, old_flow)
    repeated = await coordinator.cancel(request.provider, old_flow)
    assert cancelled.status is repeated.status is ProviderAuthorizationOutcomeStatus.CANCELLED

    second = await coordinator.start(request)
    assert second.challenge is not None
    stale = await coordinator.cancel(request.provider, old_flow)
    assert stale.error_code is ProviderAccountErrorCode.AUTHORIZATION_STALE_FLOW
    assert await coordinator.is_active(request.provider)
    await coordinator.cancel(request.provider, second.challenge.flow_id)
    await coordinator.close()


async def test_coordinator_shutdown_cleans_active_device_flow_tasks() -> None:
    driver = BlockingBrowserDriver()
    request = ProviderAuthorizationRequest(
        MusicProviderName.TIDAL, ProviderAuthorizationMethod.BROWSER_DEVICE_LINK
    )
    coordinator = ProviderAuthorizationCoordinator({(request.provider, request.method): driver})
    started = await coordinator.start(request)
    assert started.challenge is not None

    await coordinator.close()

    assert not await coordinator.is_active(request.provider)
    assert driver.cancelled == [started.challenge.flow_id]
    assert not [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and not task.done()
        and task.get_name().startswith("provider-authorization-")
    ]


def test_tidal_presentation_connect_and_callback_data_are_secret_free() -> None:
    presentation = ProviderAccountsPresentation(LocalizationService(("en", "ru"), "en"))
    tidal = ProviderAccountStatus(
        MusicProviderName.TIDAL,
        ProviderAccountState.NOT_CONFIGURED,
        datetime.now(UTC),
        authorization_methods=(ProviderAuthorizationMethod.BROWSER_DEVICE_LINK,),
    )
    deezer = ProviderAccountStatus(
        MusicProviderName.DEEZER, ProviderAccountState.NOT_CONFIGURED, datetime.now(UTC)
    )
    challenge = ProviderAuthorizationChallenge(
        MusicProviderName.TIDAL,
        "a" * 16,
        "https://login.tidal.com/device?user_code=OWNER-ONLY",
        datetime.now(UTC) + timedelta(minutes=5),
        3,
    )

    tidal_buttons = [
        button
        for row in presentation.detail_keyboard(tidal, "en").inline_keyboard
        for button in row
    ]
    deezer_buttons = [
        button
        for row in presentation.detail_keyboard(deezer, "en").inline_keyboard
        for button in row
    ]
    auth_buttons = [
        button
        for row in presentation.authorization_keyboard(challenge, "en").inline_keyboard
        for button in row
    ]
    callback_data = [button.callback_data for button in auth_buttons if button.callback_data]

    assert any(button.text == "Connect Tidal" for button in tidal_buttons)
    assert not any("Connect" in button.text for button in deezer_buttons)
    assert auth_buttons[0].url == challenge.verification_url
    assert callback_data == ["adm5:x:tidal:" + "a" * 16]
    assert "OWNER-ONLY" not in repr(callback_data)
    assert "OWNER-ONLY" not in repr(challenge)


def test_adm5_device_callbacks_are_strict_and_generation_bounded() -> None:
    value = encode_provider_accounts_callback(
        ProviderAccountsCallbackAction.CANCEL, MusicProviderName.TIDAL, "a" * 16
    )
    parsed = parse_provider_accounts_callback(value)
    assert parsed is not None
    assert parsed.action is ProviderAccountsCallbackAction.CANCEL
    assert parsed.flow_id == "a" * 16
    assert len(value.encode()) < 64
    assert parse_provider_accounts_callback("adm5:x:tidal:device-code") is None
    assert parse_provider_accounts_callback("adm5:c:deezer:extra") is None
    assert parse_provider_accounts_callback("adm5:c:tidal") is not None
