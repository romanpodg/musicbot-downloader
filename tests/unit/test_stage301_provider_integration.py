"""Stage 30.1 application integration and search-readiness regressions."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.enums import MusicProviderName, ProviderHealthStatus
from app.core.models import ProviderHealthEntry, TrackSearchCandidate
from app.core.provider_accounts import ProviderAuthorizationMethod
from app.core.search import TrackSearchRequest
from app.provider_integration import (
    DEFAULT_PROVIDER_INTEGRATIONS,
    ProviderAccountIntegrationMode,
    ProviderIntegrationRegistry,
    ProviderSearchIntegrationState,
    validate_authorization_driver_keys,
)
from app.providers.base import MusicProvider
from app.providers.onthespot.capabilities import ONTHESPOT_CAPABILITIES
from app.providers.search_adapters import RuntimeTrackSearchAdapter
from app.services.provider_search_readiness import (
    ProviderSearchReadinessStatus,
    evaluate_provider_search_readiness,
)
from app.services.track_search import TrackSearchProviderRegistry


def _default_specs():  # type: ignore[no-untyped-def]
    return tuple(DEFAULT_PROVIDER_INTEGRATIONS.specs.values())


def _registry_with(
    *, replace_provider: MusicProviderName, **changes: object
) -> ProviderIntegrationRegistry:
    return ProviderIntegrationRegistry(
        replace(spec, **changes) if spec.provider is replace_provider else spec
        for spec in _default_specs()
    )


def test_stage301_default_manifest_is_complete_and_preserves_the_production_matrix() -> None:
    registry = DEFAULT_PROVIDER_INTEGRATIONS

    assert set(registry.specs) == set(MusicProviderName)
    assert registry.enabled_search_providers() == (
        MusicProviderName.SPOTIFY,
        MusicProviderName.DEEZER,
        MusicProviderName.TIDAL,
        MusicProviderName.YOUTUBE_MUSIC,
    )
    assert registry.managed_account_providers() == (
        MusicProviderName.TIDAL,
        MusicProviderName.DEEZER,
        MusicProviderName.SPOTIFY,
    )
    assert registry.authorization_methods_for(MusicProviderName.TIDAL) == (
        ProviderAuthorizationMethod.BROWSER_DEVICE_LINK,
    )
    assert registry.authorization_methods_for(MusicProviderName.DEEZER) == (
        ProviderAuthorizationMethod.SENSITIVE_SECRET,
    )
    assert registry.authorization_methods_for(MusicProviderName.SPOTIFY) == (
        ProviderAuthorizationMethod.BROWSER_DEVICE_LINK,
        ProviderAuthorizationMethod.COMPOUND_CREDENTIALS,
    )
    for provider in (MusicProviderName.BANDCAMP, MusicProviderName.SOUNDCLOUD):
        assert registry.for_provider(provider).search is ProviderSearchIntegrationState.DEFERRED
        assert (
            registry.for_provider(provider).account is ProviderAccountIntegrationMode.NOT_REQUIRED
        )
    for provider in (MusicProviderName.APPLE_MUSIC, MusicProviderName.QOBUZ):
        assert registry.for_provider(provider).search is ProviderSearchIntegrationState.DEFERRED
        assert registry.for_provider(provider).account is ProviderAccountIntegrationMode.DEFERRED


def test_stage301_manifest_rejects_missing_duplicate_and_duplicate_orders() -> None:
    specs = _default_specs()
    with pytest.raises(ValueError, match="missing provider integrations"):
        ProviderIntegrationRegistry(specs[:-1])
    with pytest.raises(ValueError, match="duplicate provider integration"):
        ProviderIntegrationRegistry((*specs, specs[0]))
    with pytest.raises(ValueError, match="duplicate enabled search order"):
        _registry_with(replace_provider=MusicProviderName.DEEZER, search_order=0)
    with pytest.raises(ValueError, match="duplicate managed account order"):
        _registry_with(replace_provider=MusicProviderName.DEEZER, account_order=0)


def test_stage301_manifest_rejects_invalid_account_modes_and_capabilities() -> None:
    with pytest.raises(ValueError, match="account exposes authorization methods"):
        _registry_with(
            replace_provider=MusicProviderName.YOUTUBE_MUSIC,
            authorization_methods=(ProviderAuthorizationMethod.SENSITIVE_SECRET,),
        )
    with pytest.raises(ValueError, match="managed account lacks authorization methods"):
        _registry_with(replace_provider=MusicProviderName.TIDAL, authorization_methods=())

    unsupported_search = dict(ONTHESPOT_CAPABILITIES)
    unsupported_search[MusicProviderName.SPOTIFY] = replace(
        unsupported_search[MusicProviderName.SPOTIFY], search_supported=False
    )
    with pytest.raises(ValueError, match="enabled search lacks runtime capability"):
        DEFAULT_PROVIDER_INTEGRATIONS.validate_capabilities(unsupported_search)

    unauthenticated = dict(ONTHESPOT_CAPABILITIES)
    unauthenticated[MusicProviderName.TIDAL] = replace(
        unauthenticated[MusicProviderName.TIDAL], requires_auth=False
    )
    with pytest.raises(ValueError, match="managed account does not require authentication"):
        DEFAULT_PROVIDER_INTEGRATIONS.validate_capabilities(unauthenticated)


def test_stage301_manifest_declared_authorization_methods_match_composed_driver_keys() -> None:
    declared = DEFAULT_PROVIDER_INTEGRATIONS.declared_authorization_driver_keys()
    drivers = {key: object() for key in declared}

    validate_authorization_driver_keys(DEFAULT_PROVIDER_INTEGRATIONS, drivers)
    drivers.pop((MusicProviderName.TIDAL, ProviderAuthorizationMethod.BROWSER_DEVICE_LINK))
    with pytest.raises(ValueError, match="missing="):
        validate_authorization_driver_keys(DEFAULT_PROVIDER_INTEGRATIONS, drivers)
    drivers[(MusicProviderName.QOBUZ, ProviderAuthorizationMethod.BROWSER_DEVICE_LINK)] = object()
    with pytest.raises(ValueError, match="extra="):
        validate_authorization_driver_keys(DEFAULT_PROVIDER_INTEGRATIONS, drivers)


async def test_stage301_generic_adapter_rejects_invalid_runtime_candidates() -> None:
    runtime = Mock(spec=MusicProvider)
    runtime.search_tracks = AsyncMock(
        return_value=[
            TrackSearchCandidate(MusicProviderName.SPOTIFY, "one", "url", "Title", "Artist"),
            TrackSearchCandidate(MusicProviderName.SPOTIFY, "one", "url", "Duplicate", "Artist"),
            TrackSearchCandidate(MusicProviderName.DEEZER, "two", "url", "Other", "Artist"),
            TrackSearchCandidate(MusicProviderName.SPOTIFY, "bad", "url", "", "Artist"),
        ]
    )
    adapter = RuntimeTrackSearchAdapter(runtime, MusicProviderName.SPOTIFY)

    tracks = await adapter.search(TrackSearchRequest("query", limit=50))

    assert [(track.provider, track.provider_track_id) for track in tracks] == [
        (MusicProviderName.SPOTIFY, "one")
    ]
    assert runtime.search_tracks.await_args.args[0].limit == 10


def test_stage302_application_search_registry_includes_enabled_ytm_in_stable_order() -> None:
    runtime = Mock(spec=MusicProvider)
    runtime.list_searchable_providers = AsyncMock(
        return_value=(MusicProviderName.SPOTIFY, MusicProviderName.YOUTUBE_MUSIC)
    )
    registry = TrackSearchProviderRegistry(
        RuntimeTrackSearchAdapter(runtime, provider)
        for provider in DEFAULT_PROVIDER_INTEGRATIONS.enabled_search_providers()
    )

    assert registry.names == (
        MusicProviderName.SPOTIFY,
        MusicProviderName.DEEZER,
        MusicProviderName.TIDAL,
        MusicProviderName.YOUTUBE_MUSIC,
    )
    assert registry.get(MusicProviderName.YOUTUBE_MUSIC) is not None
    runtime.list_searchable_providers.assert_not_awaited()


@pytest.mark.parametrize(
    ("health_status", "expected"),
    [
        (ProviderHealthStatus.AUTH_REQUIRED, ProviderSearchReadinessStatus.AUTH_REQUIRED),
        (ProviderHealthStatus.UNAVAILABLE, ProviderSearchReadinessStatus.UNAVAILABLE),
        (ProviderHealthStatus.UNKNOWN, ProviderSearchReadinessStatus.UNKNOWN),
        (ProviderHealthStatus.ERROR, ProviderSearchReadinessStatus.ERROR),
        (ProviderHealthStatus.READY, ProviderSearchReadinessStatus.UNAVAILABLE),
    ],
)
def test_stage301_search_readiness_normalizes_non_searchable_sanitized_health(
    health_status: ProviderHealthStatus, expected: ProviderSearchReadinessStatus
) -> None:
    provider = MusicProviderName.SPOTIFY
    readiness = evaluate_provider_search_readiness(
        DEFAULT_PROVIDER_INTEGRATIONS.for_provider(provider),
        ONTHESPOT_CAPABILITIES[provider],
        runtime_searchable=False,
        health=ProviderHealthEntry(provider, health_status, True, True),
    )
    assert readiness.status is expected


def test_stage301_search_readiness_precedence_is_conservative() -> None:
    spotify = MusicProviderName.SPOTIFY
    youtube = MusicProviderName.YOUTUBE_MUSIC
    assert (
        evaluate_provider_search_readiness(
            DEFAULT_PROVIDER_INTEGRATIONS.for_provider(spotify),
            replace(ONTHESPOT_CAPABILITIES[spotify], search_supported=False),
            runtime_searchable=True,
            health=None,
        ).status
        is ProviderSearchReadinessStatus.UNSUPPORTED
    )
    assert (
        evaluate_provider_search_readiness(
            DEFAULT_PROVIDER_INTEGRATIONS.for_provider(youtube),
            ONTHESPOT_CAPABILITIES[youtube],
            runtime_searchable=True,
            health=None,
        ).status
        is ProviderSearchReadinessStatus.READY
    )
    assert (
        evaluate_provider_search_readiness(
            DEFAULT_PROVIDER_INTEGRATIONS.for_provider(spotify),
            ONTHESPOT_CAPABILITIES[spotify],
            runtime_searchable=True,
            health=None,
        ).status
        is ProviderSearchReadinessStatus.READY
    )
    assert (
        evaluate_provider_search_readiness(
            DEFAULT_PROVIDER_INTEGRATIONS.for_provider(spotify),
            ONTHESPOT_CAPABILITIES[spotify],
            runtime_searchable=False,
            health=None,
        ).status
        is ProviderSearchReadinessStatus.UNKNOWN
    )
