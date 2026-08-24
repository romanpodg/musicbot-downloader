from __future__ import annotations

import json
import logging
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import app.providers.onthespot.worker as worker_module
from app.core.enums import (
    MusicProviderName,
    ProviderHealthErrorCode,
    ProviderHealthStatus,
)
from app.core.models import ProviderHealthEntry
from app.core.provider_accounts import (
    ProviderAccountComponent,
    ProviderAccountComponentStatus,
    ProviderAccountErrorCode,
    ProviderAccountState,
    ProviderAuthorizationMethod,
    ProviderCompoundCredentialInput,
    ProviderOperationalState,
    SensitiveValue,
)
from app.i18n import LocalizationService
from app.providers.account_management import ProviderRuntimeAccountBackend
from app.providers.onthespot.worker import OnTheSpotWorker
from app.services.provider_accounts import ProviderAccountManagementService
from app.services.provider_authorization import ProviderAuthorizationCoordinator
from app.telegram.provider_accounts_presentation import (
    ProviderAccountsCallbackAction,
    ProviderAccountsPresentation,
    encode_provider_accounts_callback,
    parse_provider_accounts_callback,
)

SECRET = "stage135-deterministic-secret-never-exposed"


class PinnedConfig:
    def __init__(self, path: Path, values: dict[str, Any]) -> None:
        self._Config__cfg_path = str(path)
        self._Config__config = dict(values)
        self.save_calls = 0

    def get(self, key: str, default: Any = None) -> Any:
        return self._Config__config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._Config__config[key] = value

    def save(self) -> None:
        self.save_calls += 1
        raise AssertionError("pinned credential mutations must bypass non-atomic Config.save")


def _pinned_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, values: dict[str, Any]
) -> tuple[OnTheSpotWorker, PinnedConfig, Path]:
    config_dir = tmp_path / "onthespot"
    config_dir.mkdir(mode=0o700)
    path = config_dir / "otsconfig.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    config = PinnedConfig(path, values)
    worker = OnTheSpotWorker()
    worker._initialized = True
    worker._config = config
    worker._runtime = SimpleNamespace(account_pool=[])
    worker._accounts = SimpleNamespace(get_account_token=lambda provider: None)
    monkeypatch.setenv("ONTHESPOTDIR", str(config_dir))
    return worker, config, path


def test_atomic_update_replaces_whole_config_with_restrictive_permissions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original = {"accounts": [{"service": "qobuz"}], "unrelated": "preserved"}
    worker, config, path = _pinned_worker(monkeypatch, tmp_path, original)

    worker._persist_config_updates(
        {"accounts": [{"service": "qobuz"}, {"service": "deezer", "login": {"arl": SECRET}}]}
    )

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["unrelated"] == "preserved"
    assert persisted["accounts"][1]["login"]["arl"] == SECRET
    assert config.save_calls == 0
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
        assert stat.S_IMODE(path.parent.stat().st_mode) & 0o077 == 0
    assert not tuple(path.parent.glob(".otsconfig.json.*.tmp"))


def test_upstream_constructor_writes_are_atomically_guarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_dir = tmp_path / "onthespot"
    config_dir.mkdir()
    path = config_dir / "otsconfig.json"
    path.write_text('{"version":"old"}', encoding="utf-8")
    monkeypatch.setenv("ONTHESPOTDIR", str(config_dir))

    with worker_module._atomic_onthespot_config_writes():
        with worker_module.builtins.open(path, "w", encoding="utf-8") as stream:
            stream.write('{"version":"new"}')

    assert json.loads(path.read_text(encoding="utf-8")) == {"version": "new"}
    assert not tuple(config_dir.glob(".otsconfig.json.*.tmp"))

    with pytest.raises(RuntimeError):
        with worker_module._atomic_onthespot_config_writes():
            with worker_module.builtins.open(path, "w", encoding="utf-8") as stream:
                stream.write(SECRET)
                raise RuntimeError("interrupted")
    assert json.loads(path.read_text(encoding="utf-8")) == {"version": "new"}
    assert not tuple(config_dir.glob(".otsconfig.json.*.tmp"))


def test_interrupted_atomic_update_preserves_old_file_and_memory_without_secret_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    original = {"accounts": [{"service": "tidal", "login": {"refresh_token": "old"}}]}
    worker, config, path = _pinned_worker(monkeypatch, tmp_path, original)
    before = path.read_bytes()

    def fail_replace(source: object, destination: object) -> None:
        del source, destination
        raise OSError(SECRET)

    monkeypatch.setattr(worker_module.os, "replace", fail_replace)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(OSError):
            worker._persist_config_updates(
                {"accounts": [{"service": "tidal", "login": {"refresh_token": SECRET}}]}
            )

    assert path.read_bytes() == before
    assert config.get("accounts") == original["accounts"]
    assert not tuple(path.parent.glob(".otsconfig.json.*.tmp"))
    assert SECRET not in caplog.text


def test_startup_cleanup_removes_crash_leftovers_without_reading_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_dir = tmp_path / "onthespot"
    pairing_dir = tmp_path / "runtime" / "spotify-pairing"
    config_dir.mkdir()
    pairing_dir.mkdir(parents=True)
    config_partial = config_dir / ".otsconfig.json.crash.tmp"
    pairing_partial = pairing_dir / "corrupt.json"
    config_partial.write_text(SECRET, encoding="utf-8")
    pairing_partial.write_text(SECRET, encoding="utf-8")
    monkeypatch.setenv("ONTHESPOTDIR", str(config_dir))
    monkeypatch.setenv("MUSICBOT_TEMP_DIR", str(tmp_path / "runtime"))

    assert OnTheSpotWorker._cleanup_stale_config_artifacts() == 1
    assert OnTheSpotWorker._cleanup_stale_pairing_artifacts() == 1
    assert not config_partial.exists()
    assert not pairing_partial.exists()


def test_expired_tidal_configuration_is_observed_without_refresh_or_secret_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values = {
        "accounts": [
            {
                "service": "tidal",
                "active": True,
                "login": {
                    "username": "user",
                    "refresh_token": SECRET,
                    "token_expiry": 1,
                },
            }
        ]
    }
    worker, _, _ = _pinned_worker(monkeypatch, tmp_path, values)
    worker._append_tidal_runtime_error(values["accounts"][0], "CREDENTIAL_EXPIRED")

    result = worker.check_provider_health("tidal")

    assert result["status"] == "AUTH_REQUIRED"
    assert result["error_code"] == "CREDENTIAL_EXPIRED"
    assert SECRET not in repr(result)


def test_tidal_startup_refresh_is_bounded_atomic_and_updates_canonical_expiry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    account = {
        "uuid": "tidal-user",
        "service": "tidal",
        "active": True,
        "login": {
            "username": "safe-user",
            "country_code": "US",
            "access_token": "expired-access",
            "refresh_token": SECRET,
            "token_expiry": 1,
        },
    }
    worker, config, path = _pinned_worker(monkeypatch, tmp_path, {"accounts": [account]})

    class Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "access_token": "refreshed-access",
                "refresh_token": "rotated-refresh",
                "expires_in": 3600,
            }

    calls: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> Response:
        calls.append({"url": url, **kwargs})
        return Response()

    tidal = SimpleNamespace(
        requests=SimpleNamespace(post=post),
        AUTH_URL="https://auth.tidal.com/v1/oauth2",
        CLIENT_ID="child-client",
        AUTH=("child-client", "child-secret"),
    )
    real_import = worker_module.importlib.import_module

    def fake_import(name: str) -> Any:
        return tidal if name == "onthespot.api.tidal" else real_import(name)

    monkeypatch.setattr(worker_module.importlib, "import_module", fake_import)

    assert worker._secure_tidal_login(account) is True

    persisted = json.loads(path.read_text(encoding="utf-8"))["accounts"][0]
    assert persisted["login"]["access_token"] == "refreshed-access"
    assert persisted["login"]["refresh_token"] == "rotated-refresh"
    assert persisted["login"]["token_expiry"] > 1
    assert "expires_in" not in persisted["login"]
    assert calls[0]["timeout"] == (5.0, 10.0)
    assert config.save_calls == 0
    assert worker._runtime.account_pool[0]["status"] == "active"
    assert SECRET not in repr(worker._runtime.account_pool)


def test_rejected_tidal_refresh_preserves_previous_credentials_and_marks_expired(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    account = {
        "uuid": "tidal-user",
        "service": "tidal",
        "active": True,
        "login": {
            "username": "safe-user",
            "country_code": "US",
            "access_token": "expired-access",
            "refresh_token": SECRET,
            "token_expiry": 1,
        },
    }
    worker, _, path = _pinned_worker(monkeypatch, tmp_path, {"accounts": [account]})
    response = SimpleNamespace(status_code=401)
    tidal = SimpleNamespace(
        requests=SimpleNamespace(post=lambda *args, **kwargs: response),
        AUTH_URL="https://auth.tidal.com/v1/oauth2",
        CLIENT_ID="child-client",
        AUTH=("child-client", "child-secret"),
    )
    real_import = worker_module.importlib.import_module
    monkeypatch.setattr(
        worker_module.importlib,
        "import_module",
        lambda name: tidal if name == "onthespot.api.tidal" else real_import(name),
    )

    assert worker._secure_tidal_login(account) is False

    persisted = json.loads(path.read_text(encoding="utf-8"))["accounts"][0]
    assert persisted["login"]["refresh_token"] == SECRET
    health = worker.check_provider_health("tidal")
    assert health["error_code"] == "CREDENTIAL_EXPIRED"
    assert SECRET not in repr(health)


@pytest.mark.parametrize("provider", ["tidal", "deezer", "spotify"])
def test_reset_is_provider_scoped_atomic_and_verifies_not_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: str
) -> None:
    accounts = [
        {"uuid": name, "service": name, "active": True, "login": {"secret": SECRET}}
        for name in ("tidal", "deezer", "spotify", "qobuz")
    ]
    values = {
        "accounts": accounts,
        "active_account_number": 3,
        "spotify_webapi_override_client_id": "client-id",
        "spotify_webapi_override_client_secret": SECRET,
    }
    worker, config, path = _pinned_worker(monkeypatch, tmp_path, values)
    worker._tidal_device_flows["a" * 16] = object()  # type: ignore[assignment]

    def rebuild() -> None:
        worker._runtime.account_pool = [
            {"service": "qobuz", "status": "active", "login": {"session": None}}
        ]
        if provider == "spotify":
            worker._spotify_webapi_state = "NOT_CONFIGURED"
            worker._spotify_webapi_operational_state = "UNKNOWN"
            worker._spotify_webapi_error_code = None

    monkeypatch.setattr(worker, "_rebuild_runtime_pool", rebuild)

    result = worker.reset_provider_authentication(provider)
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert result == {"status": "disconnected"}
    assert {account["service"] for account in persisted["accounts"]} == (
        {"tidal", "deezer", "spotify", "qobuz"} - {provider}
    )
    assert config.get("active_account_number") == 2
    if provider == "spotify":
        assert config.get("spotify_webapi_override_client_id") == ""
        assert config.get("spotify_webapi_override_client_secret") == ""
    else:
        assert config.get("spotify_webapi_override_client_secret") == SECRET
    assert SECRET not in repr(result)


def test_reset_persistence_failure_preserves_every_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values = {
        "accounts": [
            {"service": "tidal", "active": True},
            {"service": "deezer", "active": True, "login": {"arl": SECRET}},
        ],
        "spotify_webapi_override_client_id": "client-id",
        "spotify_webapi_override_client_secret": SECRET,
    }
    worker, config, path = _pinned_worker(monkeypatch, tmp_path, values)
    before = path.read_bytes()

    def fail_replace(source: object, destination: object) -> None:
        del source, destination
        raise OSError(SECRET)

    monkeypatch.setattr(worker_module.os, "replace", fail_replace)
    result = worker.reset_provider_authentication("deezer")

    assert result == {"status": "failed", "error_code": "DISCONNECT_FAILED"}
    assert path.read_bytes() == before
    assert config.get("accounts") == values["accounts"]
    assert SECRET not in repr(result)


class LifecycleProbe:
    def __init__(self, health: ProviderHealthEntry) -> None:
        self.health = health
        self.reconciliations = 0

    async def check_provider_health(self, provider: MusicProviderName) -> ProviderHealthEntry:
        return ProviderHealthEntry(
            provider,
            self.health.status,
            self.health.requires_authentication,
            self.health.download_supported,
            self.health.error_code,
        )

    async def refresh_provider_health_state(self) -> None:
        raise AssertionError("status observation must not reload")

    async def get_spotify_account_components(
        self,
    ) -> tuple[ProviderAccountComponentStatus, ...]:
        return ()

    async def reconcile_provider_lifecycle(self) -> None:
        self.reconciliations += 1

    async def reset_provider_authentication(self, provider: MusicProviderName) -> bool:
        del provider
        return True


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (ProviderHealthErrorCode.SESSION_UNAVAILABLE, ProviderAccountState.DEGRADED),
        (ProviderHealthErrorCode.CREDENTIAL_EXPIRED, ProviderAccountState.EXPIRED),
        (ProviderHealthErrorCode.CREDENTIAL_INVALID, ProviderAccountState.INVALID),
        (ProviderHealthErrorCode.CREDENTIAL_REVOKED, ProviderAccountState.REVOKED),
    ],
)
async def test_lifecycle_errors_are_normalized_without_provider_secrets(
    error: ProviderHealthErrorCode, state: ProviderAccountState
) -> None:
    probe = LifecycleProbe(
        ProviderHealthEntry(
            MusicProviderName.TIDAL, ProviderHealthStatus.AUTH_REQUIRED, True, True, error
        )
    )
    backend = ProviderRuntimeAccountBackend(
        probe,
        authorization_methods={
            MusicProviderName.TIDAL: (ProviderAuthorizationMethod.BROWSER_DEVICE_LINK,)
        },
    )

    status = await backend.get_account_status(MusicProviderName.TIDAL)

    assert status.state is state
    assert status.disconnect_supported is True
    assert SECRET not in repr(status)


async def test_spotify_overall_is_degraded_when_web_api_is_rate_limited() -> None:
    probe = LifecycleProbe(
        ProviderHealthEntry(MusicProviderName.SPOTIFY, ProviderHealthStatus.READY, True, True)
    )

    async def components() -> tuple[ProviderAccountComponentStatus, ...]:
        return (
            ProviderAccountComponentStatus(
                ProviderAccountComponent.PLAYBACK, ProviderAccountState.READY
            ),
            ProviderAccountComponentStatus(
                ProviderAccountComponent.WEB_API,
                ProviderAccountState.READY,
                ProviderAccountErrorCode.SPOTIFY_WEBAPI_RATE_LIMITED,
                ProviderOperationalState.RATE_LIMITED,
            ),
        )

    probe.get_spotify_account_components = components  # type: ignore[method-assign]
    status = await ProviderRuntimeAccountBackend(probe).get_account_status(
        MusicProviderName.SPOTIFY
    )
    assert status.state is ProviderAccountState.DEGRADED


async def test_startup_reconciliation_is_explicit_and_nonfatal_at_application_boundary() -> None:
    probe = LifecycleProbe(
        ProviderHealthEntry(MusicProviderName.TIDAL, ProviderHealthStatus.READY, True, True)
    )
    backend = ProviderRuntimeAccountBackend(probe)
    await backend.reconcile_startup()
    assert probe.reconciliations == 1

    class BrokenBackend:
        async def reconcile_startup(self) -> None:
            raise RuntimeError(SECRET)

    service = ProviderAccountManagementService(
        BrokenBackend(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        ProviderAuthorizationCoordinator(),
    )
    assert await service.reconcile_startup() is False


def test_lifecycle_dtos_callbacks_and_telegram_diagnostics_are_secret_free() -> None:
    credentials = ProviderCompoundCredentialInput(
        MusicProviderName.SPOTIFY, SensitiveValue("client-id"), SensitiveValue(SECRET)
    )
    callback = encode_provider_accounts_callback(
        ProviderAccountsCallbackAction.CONFIRM_RESET, MusicProviderName.SPOTIFY
    )
    parsed = parse_provider_accounts_callback(callback)
    status = SimpleNamespace(
        provider=MusicProviderName.SPOTIFY,
        state=ProviderAccountState.DEGRADED,
        checked_at=datetime.now(UTC),
        authorization_methods=(),
        disconnect_supported=True,
        components=(),
        component_status=lambda component: None,
    )
    presentation = ProviderAccountsPresentation(LocalizationService(("en", "ru"), "en"))
    rendered = presentation.detail_text(status, "en")
    keyboard = presentation.detail_keyboard(status, "en")
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    confirmation = presentation.reset_confirmation_keyboard(MusicProviderName.SPOTIFY, "en")
    confirmation_callbacks = [
        button.callback_data for row in confirmation.inline_keyboard for button in row
    ]

    assert SECRET not in repr(credentials)
    assert callback == "adm5:y:spotify"
    assert parsed is not None and parsed.provider is MusicProviderName.SPOTIFY
    assert SECRET not in rendered
    assert "otsconfig" not in rendered.lower()
    assert "adm5:z:spotify" in callbacks
    assert confirmation_callbacks == ["adm5:y:spotify", "adm5:d:spotify"]
    assert SECRET not in repr((callbacks, confirmation_callbacks))
