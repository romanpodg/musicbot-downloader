"""Application-level provider integration declarations.

This manifest deliberately sits above the isolated runtime capability table.
It decides which runtime features the application intentionally exposes; it
does not report a provider session's health or a TrackSource's readiness.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from app.core.enums import MusicProviderName
from app.core.models import ProviderCapabilities
from app.core.provider_accounts import ProviderAuthorizationMethod


class ProviderSearchIntegrationState(StrEnum):
    ENABLED = "ENABLED"
    DEFERRED = "DEFERRED"


class ProviderAccountIntegrationMode(StrEnum):
    MANAGED = "MANAGED"
    NOT_REQUIRED = "NOT_REQUIRED"
    DEFERRED = "DEFERRED"


@dataclass(frozen=True, slots=True)
class ProviderIntegrationSpec:
    """One application's intentional integration decision for one provider."""

    provider: MusicProviderName
    search: ProviderSearchIntegrationState
    account: ProviderAccountIntegrationMode
    search_order: int | None = None
    account_order: int | None = None
    authorization_methods: tuple[ProviderAuthorizationMethod, ...] = ()


class ProviderIntegrationRegistry:
    """Immutable, validated application feature manifest.

    Ordering values are only stable search and account-presentation ordering.
    They are never download, quality, or canonical-identity ranking inputs.
    """

    def __init__(self, specs: Iterable[ProviderIntegrationSpec]) -> None:
        by_provider: dict[MusicProviderName, ProviderIntegrationSpec] = {}
        for spec in specs:
            if spec.provider in by_provider:
                raise ValueError(f"duplicate provider integration: {spec.provider.value}")
            by_provider[spec.provider] = spec
        missing = sorted(
            provider.value for provider in MusicProviderName if provider not in by_provider
        )
        if missing:
            raise ValueError(f"missing provider integrations: {', '.join(missing)}")
        self._specs = MappingProxyType(by_provider)
        self._validate_structure()

    @property
    def specs(self) -> Mapping[MusicProviderName, ProviderIntegrationSpec]:
        return self._specs

    def for_provider(self, provider: MusicProviderName) -> ProviderIntegrationSpec:
        return self._specs[provider]

    def enabled_search_providers(self) -> tuple[MusicProviderName, ...]:
        return tuple(
            spec.provider
            for spec in sorted(
                (
                    spec
                    for spec in self._specs.values()
                    if spec.search is ProviderSearchIntegrationState.ENABLED
                ),
                key=lambda spec: spec.search_order if spec.search_order is not None else -1,
            )
        )

    def managed_account_providers(self) -> tuple[MusicProviderName, ...]:
        return tuple(
            spec.provider
            for spec in sorted(
                (
                    spec
                    for spec in self._specs.values()
                    if spec.account is ProviderAccountIntegrationMode.MANAGED
                ),
                key=lambda spec: spec.account_order if spec.account_order is not None else -1,
            )
        )

    def authorization_methods_for(
        self, provider: MusicProviderName
    ) -> tuple[ProviderAuthorizationMethod, ...]:
        return self.for_provider(provider).authorization_methods

    def authorization_methods_by_provider(
        self,
    ) -> Mapping[MusicProviderName, tuple[ProviderAuthorizationMethod, ...]]:
        return MappingProxyType(
            {
                provider: spec.authorization_methods
                for provider, spec in self._specs.items()
                if spec.authorization_methods
            }
        )

    def declared_authorization_driver_keys(
        self,
    ) -> frozenset[tuple[MusicProviderName, ProviderAuthorizationMethod]]:
        return frozenset(
            (provider, method)
            for provider, spec in self._specs.items()
            for method in spec.authorization_methods
        )

    def validate_capabilities(
        self, capabilities: Mapping[MusicProviderName, ProviderCapabilities]
    ) -> None:
        missing = sorted(
            provider.value for provider in MusicProviderName if provider not in capabilities
        )
        if missing:
            raise ValueError(f"missing provider capabilities: {', '.join(missing)}")
        for spec in self._specs.values():
            capability = capabilities[spec.provider]
            if (
                spec.search is ProviderSearchIntegrationState.ENABLED
                and not capability.search_supported
            ):
                raise ValueError(f"enabled search lacks runtime capability: {spec.provider.value}")
            if (
                spec.account is ProviderAccountIntegrationMode.MANAGED
                and capability.requires_auth is not True
            ):
                raise ValueError(
                    f"managed account does not require authentication: {spec.provider.value}"
                )

    def _validate_structure(self) -> None:
        _validate_order(
            "enabled search",
            (
                spec.search_order
                for spec in self._specs.values()
                if spec.search is ProviderSearchIntegrationState.ENABLED
            ),
        )
        _validate_order(
            "managed account",
            (
                spec.account_order
                for spec in self._specs.values()
                if spec.account is ProviderAccountIntegrationMode.MANAGED
            ),
        )
        for spec in self._specs.values():
            if spec.search is ProviderSearchIntegrationState.ENABLED and spec.search_order is None:
                raise ValueError(f"enabled search lacks order: {spec.provider.value}")
            if spec.account is ProviderAccountIntegrationMode.MANAGED:
                if spec.account_order is None:
                    raise ValueError(f"managed account lacks order: {spec.provider.value}")
                if not spec.authorization_methods:
                    raise ValueError(
                        f"managed account lacks authorization methods: {spec.provider.value}"
                    )
            elif spec.authorization_methods:
                raise ValueError(
                    f"{spec.account.value} account exposes authorization methods: "
                    f"{spec.provider.value}"
                )


def validate_authorization_driver_keys(
    registry: ProviderIntegrationRegistry,
    drivers: Mapping[tuple[MusicProviderName, ProviderAuthorizationMethod], object],
) -> None:
    """Require explicit composition drivers to match manifest declarations exactly."""

    declared = registry.declared_authorization_driver_keys()
    actual = frozenset(drivers)
    if actual != declared:
        missing = sorted(
            f"{provider.value}:{method.value}" for provider, method in declared - actual
        )
        extra = sorted(f"{provider.value}:{method.value}" for provider, method in actual - declared)
        details = []
        if missing:
            details.append(f"missing={','.join(missing)}")
        if extra:
            details.append(f"extra={','.join(extra)}")
        raise ValueError(f"authorization driver manifest mismatch ({'; '.join(details)})")


def _validate_order(label: str, values: Iterable[int | None]) -> None:
    normalized = tuple(values)
    if any(value is None for value in normalized):
        raise ValueError(f"{label} order is required")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"duplicate {label} order")


DEFAULT_PROVIDER_INTEGRATIONS = ProviderIntegrationRegistry(
    (
        ProviderIntegrationSpec(
            MusicProviderName.SPOTIFY,
            ProviderSearchIntegrationState.ENABLED,
            ProviderAccountIntegrationMode.MANAGED,
            search_order=0,
            account_order=2,
            authorization_methods=(
                ProviderAuthorizationMethod.BROWSER_DEVICE_LINK,
                ProviderAuthorizationMethod.COMPOUND_CREDENTIALS,
            ),
        ),
        ProviderIntegrationSpec(
            MusicProviderName.DEEZER,
            ProviderSearchIntegrationState.ENABLED,
            ProviderAccountIntegrationMode.MANAGED,
            search_order=1,
            account_order=1,
            authorization_methods=(ProviderAuthorizationMethod.SENSITIVE_SECRET,),
        ),
        ProviderIntegrationSpec(
            MusicProviderName.TIDAL,
            ProviderSearchIntegrationState.ENABLED,
            ProviderAccountIntegrationMode.MANAGED,
            search_order=2,
            account_order=0,
            authorization_methods=(ProviderAuthorizationMethod.BROWSER_DEVICE_LINK,),
        ),
        ProviderIntegrationSpec(
            MusicProviderName.YOUTUBE_MUSIC,
            ProviderSearchIntegrationState.ENABLED,
            ProviderAccountIntegrationMode.NOT_REQUIRED,
            search_order=3,
        ),
        ProviderIntegrationSpec(
            MusicProviderName.BANDCAMP,
            ProviderSearchIntegrationState.ENABLED,
            ProviderAccountIntegrationMode.NOT_REQUIRED,
            # Append the public provider so the established provider order is
            # unchanged. This is search presentation order only; Stage 25
            # still ranks feasible sources by their verified quality plan.
            search_order=6,
        ),
        ProviderIntegrationSpec(
            MusicProviderName.SOUNDCLOUD,
            ProviderSearchIntegrationState.DEFERRED,
            ProviderAccountIntegrationMode.NOT_REQUIRED,
        ),
        ProviderIntegrationSpec(
            MusicProviderName.APPLE_MUSIC,
            ProviderSearchIntegrationState.ENABLED,
            ProviderAccountIntegrationMode.MANAGED,
            search_order=5,
            account_order=4,
            authorization_methods=(ProviderAuthorizationMethod.APPLE_MUSIC_SESSION_TOKEN,),
        ),
        ProviderIntegrationSpec(
            MusicProviderName.QOBUZ,
            ProviderSearchIntegrationState.ENABLED,
            ProviderAccountIntegrationMode.MANAGED,
            search_order=4,
            account_order=3,
            authorization_methods=(ProviderAuthorizationMethod.QOBUZ_CREDENTIALS,),
        ),
    )
)
