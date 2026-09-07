"""Focused Stage 30.4.1 Apple Music vertical-contract tests."""

from types import SimpleNamespace

import pytest

from app.core.enums import (
    DownloadPlanOperation,
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    ProviderRuntimeStatus,
    QualityProfile,
)
from app.core.models import DownloadProviderCandidate, NativeMediaInfo
from app.core.provider_accounts import (
    ProviderAccountErrorCode,
    ProviderAccountState,
    ProviderAccountStatus,
    ProviderAuthorizationMethod,
    ProviderAuthorizationOutcomeStatus,
    ProviderSessionTokenInput,
    SensitiveValue,
)
from app.core.quality import plans_for_candidate
from app.provider_integration import DEFAULT_PROVIDER_INTEGRATIONS
from app.providers.apple_music_authorization import (
    AppleMusicAuthorizationDriver,
    AppleMusicAuthorizationResult,
)
from app.providers.onthespot.capabilities import ONTHESPOT_CAPABILITIES


def test_apple_is_promoted_in_manifest_without_changing_deferred_providers() -> None:
    assert DEFAULT_PROVIDER_INTEGRATIONS.enabled_search_providers() == (
        MusicProviderName.SPOTIFY,
        MusicProviderName.DEEZER,
        MusicProviderName.TIDAL,
        MusicProviderName.YOUTUBE_MUSIC,
        MusicProviderName.QOBUZ,
        MusicProviderName.APPLE_MUSIC,
    )
    assert DEFAULT_PROVIDER_INTEGRATIONS.managed_account_providers() == (
        MusicProviderName.TIDAL,
        MusicProviderName.DEEZER,
        MusicProviderName.SPOTIFY,
        MusicProviderName.QOBUZ,
        MusicProviderName.APPLE_MUSIC,
    )
    spec = DEFAULT_PROVIDER_INTEGRATIONS.for_provider(MusicProviderName.APPLE_MUSIC)
    assert spec.search_order == 5
    assert spec.account_order == 4
    assert spec.authorization_methods == (ProviderAuthorizationMethod.APPLE_MUSIC_SESSION_TOKEN,)


@pytest.mark.asyncio
async def test_apple_driver_requires_verified_premium_readiness() -> None:
    boundary = SimpleNamespace(
        authorize_apple_music_session=lambda token: AppleMusicAuthorizationResult(True)
    )
    boundary.authorize_apple_music_session = _async_result
    backend = SimpleNamespace(
        reload_account_state=_async_noop,
        get_account_status=lambda provider: _async_status(provider),
    )
    driver = AppleMusicAuthorizationDriver(boundary, backend)
    assert driver.capabilities.supports_entitlement_check
    outcome = await driver.authorize_session_token(
        ProviderSessionTokenInput(MusicProviderName.APPLE_MUSIC, SensitiveValue("token"))
    )
    assert outcome.status is ProviderAuthorizationOutcomeStatus.READY


@pytest.mark.asyncio
async def test_apple_driver_maps_subscription_failure_without_secret_echo() -> None:
    boundary = SimpleNamespace(authorize_apple_music_session=_async_result)
    backend = SimpleNamespace(
        reload_account_state=_async_noop,
        get_account_status=lambda provider: _async_status(
            provider, ProviderAccountState.INVALID, ProviderAccountErrorCode.SUBSCRIPTION_REQUIRED
        ),
    )
    outcome = await AppleMusicAuthorizationDriver(boundary, backend).authorize_session_token(
        ProviderSessionTokenInput(MusicProviderName.APPLE_MUSIC, SensitiveValue("secret-token"))
    )
    assert outcome.error_code is ProviderAccountErrorCode.SUBSCRIPTION_REQUIRED
    assert "secret-token" not in repr(outcome)


@pytest.mark.parametrize(
    "profile",
    [
        QualityProfile.AAC_128,
        QualityProfile.MP3_128,
        QualityProfile.MP3_320,
        QualityProfile.LOSSLESS,
    ],
)
def test_apple_native_aac256_only_satisfies_exact_aac256(profile: QualityProfile) -> None:
    candidate = DownloadProviderCandidate(
        1,
        2,
        MusicProviderName.APPLE_MUSIC,
        "123",
        ProviderRuntimeStatus.AVAILABLE,
        ONTHESPOT_CAPABILITIES[MusicProviderName.APPLE_MUSIC],
        NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 256),
    )
    plans = plans_for_candidate(candidate, profile)
    assert not plans


def test_apple_native_aac256_is_direct_for_exact_profile() -> None:
    candidate = DownloadProviderCandidate(
        1,
        2,
        MusicProviderName.APPLE_MUSIC,
        "123",
        ProviderRuntimeStatus.AVAILABLE,
        ONTHESPOT_CAPABILITIES[MusicProviderName.APPLE_MUSIC],
        NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 256),
    )
    plan = plans_for_candidate(candidate, QualityProfile.AAC_256)[0]
    assert plan.operation is DownloadPlanOperation.DIRECT


async def _async_result(token: SensitiveValue) -> AppleMusicAuthorizationResult:
    assert repr(token) == "SensitiveValue('[REDACTED]')"
    return AppleMusicAuthorizationResult(True)


async def _async_noop() -> None:
    return None


async def _async_status(
    provider: MusicProviderName,
    state: ProviderAccountState = ProviderAccountState.READY,
    error_code: ProviderAccountErrorCode | None = None,
) -> ProviderAccountStatus:
    from app.storage.models.base import utc_now

    return ProviderAccountStatus(provider, state, utc_now(), error_code=error_code)
