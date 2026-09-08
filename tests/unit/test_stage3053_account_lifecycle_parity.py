"""Stage 30.5.3 managed-provider reset and lifecycle parity regressions."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.enums import MusicProviderName
from app.providers.onthespot.worker import OnTheSpotWorker


class _PinnedConfig:
    def __init__(self, path: Path, values: dict[str, Any]) -> None:
        self._Config__cfg_path = str(path)
        self._Config__config = dict(values)

    def get(self, key: str, default: Any = None) -> Any:
        return self._Config__config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._Config__config[key] = value


def _worker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[OnTheSpotWorker, Path]:
    root = tmp_path / "onthespot"
    root.mkdir(mode=0o700)
    path = root / "otsconfig.json"
    values = {
        "accounts": [
            {
                "uuid": "apple",
                "service": "apple_music",
                "active": True,
                "login": {"media_user_token": "STAGE3053_DO_NOT_LEAK_APPLE_TOKEN"},
            },
            {
                "uuid": "qobuz",
                "service": "qobuz",
                "active": True,
                "login": {
                    "email": "q@example.invalid",
                    "password": "STAGE3053_DO_NOT_LEAK_QOBUZ_PASSWORD",
                },
            },
            {"uuid": "tidal", "service": "tidal", "active": True},
        ],
        "active_account_number": 0,
    }
    path.write_text(json.dumps(values), encoding="utf-8")
    config = _PinnedConfig(path, values)
    worker = OnTheSpotWorker()
    worker._initialized = True
    worker._config = config
    worker._runtime = SimpleNamespace(account_pool=[])
    worker._accounts = SimpleNamespace(get_account_token=lambda provider: None)
    monkeypatch.setenv("ONTHESPOTDIR", str(root))
    return worker, path


@pytest.mark.parametrize("provider", ["apple_music", "qobuz"])
def test_reset_parity_is_provider_scoped_and_redacted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: str
) -> None:
    worker, path = _worker(monkeypatch, tmp_path)
    monkeypatch.setattr(
        worker, "_rebuild_runtime_pool", lambda: worker._runtime.account_pool.clear()
    )

    result = worker.reset_provider_authentication(provider)

    assert result == {"status": "disconnected"}
    persisted = json.loads(path.read_text(encoding="utf-8"))
    services = {item["service"] for item in persisted["accounts"]}
    assert provider not in services
    assert services == {"apple_music", "qobuz", "tidal"} - {provider}
    assert "STAGE3053_DO_NOT_LEAK" not in repr(result)
    health = worker.check_provider_health(provider)
    assert health["status"] == "AUTH_REQUIRED"
    assert health["error_code"] == "AUTH_NOT_CONFIGURED"
    assert "STAGE3053_DO_NOT_LEAK" not in repr(health)


@pytest.mark.parametrize("provider", ["youtube_music", "bandcamp", "soundcloud"])
def test_unmanaged_and_deferred_providers_remain_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: str
) -> None:
    worker, _ = _worker(monkeypatch, tmp_path)
    assert worker.reset_provider_authentication(provider) == {
        "status": "failed",
        "error_code": "DISCONNECT_UNSUPPORTED",
    }


def test_managed_provider_enum_values_match_lifecycle_contract() -> None:
    assert {
        p.value
        for p in (
            MusicProviderName.TIDAL,
            MusicProviderName.DEEZER,
            MusicProviderName.SPOTIFY,
            MusicProviderName.QOBUZ,
            MusicProviderName.APPLE_MUSIC,
        )
    } == {"tidal", "deezer", "spotify", "qobuz", "apple_music"}
