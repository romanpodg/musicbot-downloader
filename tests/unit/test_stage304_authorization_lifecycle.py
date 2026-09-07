"""Stage 30.4 authorization lifecycle abstraction regressions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.enums import MusicProviderName
from app.core.provider_accounts import (
    AuthorizationCapabilities,
    ProviderAuthorizationLifecycleOperation,
    ProviderAuthorizationMethod,
    ProviderAuthorizationOutcomeStatus,
)
from app.provider_integration import DEFAULT_PROVIDER_INTEGRATIONS
from app.providers.deezer_authorization import DeezerArlAuthorizationDriver
from app.providers.qobuz_authorization import QobuzAuthorizationDriver
from app.providers.spotify_authorization import (
    SpotifyPlaybackAuthorizationDriver,
    SpotifyWebApiAuthorizationDriver,
)
from app.providers.tidal_authorization import TidalDeviceAuthorizationDriver
from app.services.provider_authorization import ProviderAuthorizationCoordinator


def test_capabilities_are_explicit_and_lifecycle_oriented() -> None:
    assert AuthorizationCapabilities().operations == frozenset()
    assert ProviderAuthorizationLifecycleOperation.SESSION_VALIDATION.value == (
        "SESSION_VALIDATION"
    )
    drivers = (
        SpotifyPlaybackAuthorizationDriver,
        SpotifyWebApiAuthorizationDriver,
        TidalDeviceAuthorizationDriver,
        DeezerArlAuthorizationDriver,
        QobuzAuthorizationDriver,
    )
    for driver in drivers:
        assert isinstance(driver.capabilities, AuthorizationCapabilities)
        assert driver.capabilities.supports_validate
        assert driver.capabilities.supports_readiness_projection


def test_existing_manifest_remains_unchanged_and_explicit() -> None:
    assert DEFAULT_PROVIDER_INTEGRATIONS.authorization_methods_for(
        MusicProviderName.APPLE_MUSIC
    ) == (ProviderAuthorizationMethod.APPLE_MUSIC_SESSION_TOKEN,)
    assert (
        DEFAULT_PROVIDER_INTEGRATIONS.for_provider(
            MusicProviderName.YOUTUBE_MUSIC
        ).authorization_methods
        == ()
    )
    assert DEFAULT_PROVIDER_INTEGRATIONS.declared_authorization_driver_keys()


@pytest.mark.asyncio
async def test_optional_lifecycle_operations_fail_closed_without_driver_method() -> None:
    coordinator = ProviderAuthorizationCoordinator(
        {
            (
                MusicProviderName.QOBUZ,
                ProviderAuthorizationMethod.QOBUZ_CREDENTIALS,
            ): SimpleNamespace(capabilities=AuthorizationCapabilities(supports_refresh=True))
        }
    )
    assert coordinator.supports(
        MusicProviderName.QOBUZ,
        ProviderAuthorizationMethod.QOBUZ_CREDENTIALS,
        ProviderAuthorizationLifecycleOperation.REFRESH,
    )
    outcome = await coordinator.invoke_lifecycle(
        MusicProviderName.QOBUZ,
        ProviderAuthorizationMethod.QOBUZ_CREDENTIALS,
        ProviderAuthorizationLifecycleOperation.REFRESH,
    )
    assert outcome.status is ProviderAuthorizationOutcomeStatus.UNSUPPORTED
