from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.enums import (
    MusicProviderName,
    ProviderHealthErrorCode,
    ProviderHealthStatus,
)
from app.core.models import ProviderHealthEntry, ProviderHealthSnapshot
from app.i18n import LocalizationService
from app.providers.onthespot.worker import OnTheSpotWorker
from app.telegram.provider_health_presentation import (
    ProviderHealthCallbackAction,
    ProviderHealthPresentation,
    encode_provider_health_callback,
    parse_provider_health_callback,
)


def test_provider_health_has_no_execution_dependency_edge() -> None:
    from pathlib import Path

    for relative in (
        "app/services/provider_resolution.py",
        "app/services/quality_resolution.py",
        "app/services/download_pipeline.py",
        "app/services/workers.py",
        "app/services/telegram_cache.py",
        "app/services/telegram_requests.py",
        "app/services/telegram_delivery.py",
        "app/services/delivery.py",
        "app/services/singleflight.py",
        "app/services/track_resolution.py",
    ):
        source = Path(relative).read_text(encoding="utf-8")
        assert "provider_health" not in source
        assert "ProviderHealth" not in source


class FakeAccounts:
    def __init__(self, token: Any) -> None:
        self.token = token

    def get_account_token(self, provider: str) -> Any:
        del provider
        return self.token


def _worker_health(
    provider: str,
    *,
    token: Any = None,
    configured: bool = True,
    active: bool = True,
    account_type: str = "premium",
) -> dict[str, Any]:
    worker = OnTheSpotWorker()
    worker._initialized = True
    account = {
        "service": provider,
        "status": "active" if active else "error",
        "account_type": account_type,
        "uuid": "credential-like-secret",
        "login": token,
    }
    worker._runtime = SimpleNamespace(account_pool=[account])
    worker._config = SimpleNamespace(
        get=lambda key, default=None: (
            [{"service": provider, "active": True}] if configured and key == "accounts" else default
        )
    )
    worker._accounts = FakeAccounts(token)
    return worker.check_provider_health(provider)


def test_worker_health_normalizes_auth_tokenless_subscription_and_unknown() -> None:
    assert _worker_health("bandcamp", token=None)["status"] == "READY"
    assert _worker_health("spotify", configured=False, active=False) == {
        "status": "AUTH_REQUIRED",
        "requires_authentication": True,
        "download_supported": True,
        "error_code": "AUTH_NOT_CONFIGURED",
    }
    assert _worker_health("spotify", token=object())["status"] == "READY"
    assert _worker_health("spotify", token=None)["error_code"] == "SESSION_UNAVAILABLE"
    assert (
        _worker_health("apple_music", token=object(), account_type="free")["error_code"]
        == "SUBSCRIPTION_REQUIRED"
    )
    assert _worker_health("qobuz", token={"access_token": "never-returned"}) == {
        "status": "UNKNOWN",
        "requires_authentication": True,
        "download_supported": True,
        "error_code": "SESSION_UNVERIFIED",
    }


@pytest.mark.parametrize("action", list(ProviderHealthCallbackAction))
def test_callback_codec_round_trip_and_size(action: ProviderHealthCallbackAction) -> None:
    encoded = encode_provider_health_callback(action)
    assert len(encoded.encode()) <= 64
    assert parse_provider_health_callback(encoded) is action


@pytest.mark.parametrize(
    "value", [None, "", "adm4", "adm4:x", "adm4:h:extra", "adm3:h", "adm4:" + "x" * 65]
)
def test_callback_codec_rejects_malformed(value: str | None) -> None:
    assert parse_provider_health_callback(value) is None


def test_english_and_russian_snapshot_render_without_diagnostics_or_secrets() -> None:
    snapshot = ProviderHealthSnapshot(
        datetime(2026, 8, 20, 19, 42, 31, tzinfo=UTC),
        (
            ProviderHealthEntry(
                MusicProviderName.QOBUZ,
                ProviderHealthStatus.UNKNOWN,
                True,
                True,
                ProviderHealthErrorCode.SESSION_UNVERIFIED,
            ),
            ProviderHealthEntry(
                MusicProviderName.BANDCAMP,
                ProviderHealthStatus.READY,
                False,
                True,
            ),
        ),
        12,
    )
    presentation = ProviderHealthPresentation(LocalizationService(("en", "ru"), "en"))
    english = presentation.snapshot_text(snapshot, "en")
    russian = presentation.snapshot_text(snapshot, "ru")
    assert "Provider Health" in english and "Qobuz — Unknown" in english
    assert "Состояние провайдеров" in russian and "Qobuz — Неизвестно" in russian
    for rendered in (english, russian):
        assert "SESSION_UNVERIFIED" not in rendered
        assert "credential-like-secret" not in rendered
        assert len(rendered) <= 4096
