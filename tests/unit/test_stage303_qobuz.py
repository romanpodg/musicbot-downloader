"""Stage 30.3 Qobuz manifest, authorization, and readiness regressions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.enums import MusicProviderName, ProviderHealthStatus
from app.core.provider_accounts import (
    ProviderAccountErrorCode,
    ProviderAccountState,
    ProviderAuthorizationOutcomeStatus,
    ProviderQobuzCredentialInput,
    SensitiveValue,
)
from app.provider_integration import (
    DEFAULT_PROVIDER_INTEGRATIONS,
    ProviderAccountIntegrationMode,
    ProviderSearchIntegrationState,
)
from app.providers.onthespot.worker import OnTheSpotWorker
from app.providers.qobuz_authorization import QobuzAuthorizationDriver, QobuzAuthorizationResult


def test_qobuz_is_promoted_without_provider_priority() -> None:
    spec = DEFAULT_PROVIDER_INTEGRATIONS.for_provider(MusicProviderName.QOBUZ)
    assert spec.search is ProviderSearchIntegrationState.ENABLED
    assert spec.account is ProviderAccountIntegrationMode.MANAGED
    assert spec.authorization_methods[0].value == "QOBUZ_CREDENTIALS"
    assert spec.search_order == 4
    assert spec.account_order == 3
    assert DEFAULT_PROVIDER_INTEGRATIONS.enabled_search_providers()[-3] is MusicProviderName.QOBUZ


@pytest.mark.asyncio
async def test_qobuz_driver_requires_runtime_ready_after_child_persistence() -> None:
    boundary = SimpleNamespace(
        authorize_qobuz_credentials=AsyncMock(return_value=QobuzAuthorizationResult(True))
    )
    backend = SimpleNamespace(
        reload_account_state=AsyncMock(),
        get_account_status=AsyncMock(
            return_value=SimpleNamespace(state=ProviderAccountState.READY, error_code=None)
        ),
    )
    driver = QobuzAuthorizationDriver(boundary, backend)
    outcome = await driver.authorize_qobuz_credentials(
        ProviderQobuzCredentialInput(
            MusicProviderName.QOBUZ, SensitiveValue("user@example.com"), SensitiveValue("pw")
        )
    )
    assert outcome.status is ProviderAuthorizationOutcomeStatus.READY
    backend.reload_account_state.assert_awaited_once()


def test_qobuz_health_without_verifiable_runtime_stays_unknown() -> None:
    worker = OnTheSpotWorker()
    worker._initialized = True
    worker._runtime = SimpleNamespace(
        account_pool=[
            {
                "service": "qobuz",
                "status": "active",
                "account_type": "premium",
                "login": {"session": object()},
            }
        ]
    )
    worker._config = SimpleNamespace(
        get=lambda key, default=None: [{"service": "qobuz", "active": True}]
    )
    worker._accounts = SimpleNamespace(get_account_token=lambda provider: object())
    health = worker.check_provider_health("qobuz")
    assert health["status"] == ProviderHealthStatus.UNKNOWN
    assert health["error_code"] == ProviderAccountErrorCode.SESSION_UNVERIFIED
