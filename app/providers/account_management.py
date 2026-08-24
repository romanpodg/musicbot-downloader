"""Provider-account backend contract and runtime-health adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from typing import Final, Protocol

from app.core.enums import MusicProviderName, ProviderHealthErrorCode, ProviderHealthStatus
from app.core.models import ProviderHealthEntry
from app.core.provider_accounts import (
    ProviderAccountComponent,
    ProviderAccountComponentStatus,
    ProviderAccountErrorCode,
    ProviderAccountState,
    ProviderAccountStatus,
    ProviderAuthorizationMethod,
    ProviderDisconnectOutcome,
    ProviderDisconnectOutcomeStatus,
    ProviderOperationalState,
)
from app.storage.models.base import utc_now

MANAGED_PROVIDER_ORDER: Final = (
    MusicProviderName.TIDAL,
    MusicProviderName.DEEZER,
    MusicProviderName.SPOTIFY,
)
PROVIDER_ACCOUNT_STATUS_TIMEOUT_SECONDS = 15.0
PROVIDER_ACCOUNT_REFRESH_TIMEOUT_SECONDS = 60.0


class ProviderAccountRuntimeProbe(Protocol):
    """Sanitized child/runtime boundary reused by account management."""

    async def refresh_provider_health_state(self) -> None: ...

    async def check_provider_health(self, provider: MusicProviderName) -> ProviderHealthEntry: ...

    async def get_spotify_account_components(
        self,
    ) -> tuple[ProviderAccountComponentStatus, ...]: ...

    async def reconcile_provider_lifecycle(self) -> None: ...

    async def reset_provider_authentication(self, provider: MusicProviderName) -> bool: ...


class ProviderAccountBackendError(Exception):
    """Sanitized backend failure that never retains an upstream exception."""

    def __init__(self, code: ProviderAccountErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


class ProviderAccountBackend(Protocol):
    async def get_account_status(self, provider: MusicProviderName) -> ProviderAccountStatus: ...

    async def reload_account_state(self) -> None: ...

    async def reconcile_startup(self) -> None: ...

    async def disconnect_account(
        self, provider: MusicProviderName
    ) -> ProviderDisconnectOutcome: ...


class ProviderRuntimeAccountBackend:
    """Normalize existing child-process runtime facts into account DTOs."""

    def __init__(
        self,
        probe: ProviderAccountRuntimeProbe,
        *,
        authorization_methods: Mapping[MusicProviderName, tuple[ProviderAuthorizationMethod, ...]]
        | None = None,
    ) -> None:
        self._probe = probe
        self._authorization_methods = dict(authorization_methods or {})

    async def get_account_status(self, provider: MusicProviderName) -> ProviderAccountStatus:
        if provider not in MANAGED_PROVIDER_ORDER:
            return ProviderAccountStatus(
                provider,
                ProviderAccountState.UNSUPPORTED,
                utc_now(),
                error_code=ProviderAccountErrorCode.PROVIDER_UNSUPPORTED,
            )
        try:
            async with asyncio.timeout(PROVIDER_ACCOUNT_STATUS_TIMEOUT_SECONDS):
                health = await self._probe.check_provider_health(provider)
        except TimeoutError:
            return _recovering_status(provider, self._authorization_methods.get(provider, ()))
        except Exception:
            return _recovering_status(provider, self._authorization_methods.get(provider, ()))
        if health.provider is not provider:
            return _failed_status(provider, ProviderAccountErrorCode.INVALID_BACKEND_RESPONSE)
        status = replace(
            _normalize_health(health, self._authorization_methods.get(provider, ())),
            disconnect_supported=True,
        )
        if provider is MusicProviderName.SPOTIFY:
            component_probe = getattr(self._probe, "get_spotify_account_components", None)
            if not callable(component_probe):
                return status
            try:
                async with asyncio.timeout(PROVIDER_ACCOUNT_STATUS_TIMEOUT_SECONDS):
                    components = await component_probe()
            except Exception:
                return _failed_status(provider, ProviderAccountErrorCode.STATUS_CHECK_FAILED)
            return replace(
                status,
                state=_spotify_overall_state(status.state, components),
                components=components,
            )
        return status

    async def reload_account_state(self) -> None:
        try:
            async with asyncio.timeout(PROVIDER_ACCOUNT_REFRESH_TIMEOUT_SECONDS):
                await self._probe.refresh_provider_health_state()
        except Exception:
            raise ProviderAccountBackendError(ProviderAccountErrorCode.REFRESH_FAILED) from None

    async def reconcile_startup(self) -> None:
        try:
            async with asyncio.timeout(PROVIDER_ACCOUNT_REFRESH_TIMEOUT_SECONDS):
                await self._probe.reconcile_provider_lifecycle()
        except Exception:
            raise ProviderAccountBackendError(
                ProviderAccountErrorCode.LIFECYCLE_RECONCILIATION_FAILED
            ) from None

    async def disconnect_account(self, provider: MusicProviderName) -> ProviderDisconnectOutcome:
        if provider not in MANAGED_PROVIDER_ORDER:
            return ProviderDisconnectOutcome(
                provider,
                ProviderDisconnectOutcomeStatus.UNSUPPORTED,
                ProviderAccountErrorCode.DISCONNECT_UNSUPPORTED,
            )
        try:
            async with asyncio.timeout(PROVIDER_ACCOUNT_REFRESH_TIMEOUT_SECONDS):
                disconnected = await self._probe.reset_provider_authentication(provider)
            if not disconnected:
                raise ProviderAccountBackendError(ProviderAccountErrorCode.DISCONNECT_FAILED)
            status = await self.get_account_status(provider)
        except Exception:
            return ProviderDisconnectOutcome(
                provider,
                ProviderDisconnectOutcomeStatus.FAILED,
                ProviderAccountErrorCode.DISCONNECT_FAILED,
            )
        if status.state is not ProviderAccountState.NOT_CONFIGURED:
            return ProviderDisconnectOutcome(
                provider,
                ProviderDisconnectOutcomeStatus.FAILED,
                ProviderAccountErrorCode.DISCONNECT_FAILED,
            )
        if provider is MusicProviderName.SPOTIFY and any(
            component.state is not ProviderAccountState.NOT_CONFIGURED
            and component.component is ProviderAccountComponent.WEB_API
            for component in status.components
        ):
            return ProviderDisconnectOutcome(
                provider,
                ProviderDisconnectOutcomeStatus.FAILED,
                ProviderAccountErrorCode.DISCONNECT_FAILED,
            )
        return ProviderDisconnectOutcome(provider, ProviderDisconnectOutcomeStatus.DISCONNECTED)


def _normalize_health(
    health: ProviderHealthEntry,
    authorization_methods: tuple[ProviderAuthorizationMethod, ...],
) -> ProviderAccountStatus:
    state = ProviderAccountState.ERROR
    code = _normalize_error(health.error_code)
    if health.status is ProviderHealthStatus.READY:
        state = ProviderAccountState.READY
        code = None
    elif health.status is ProviderHealthStatus.AUTH_REQUIRED:
        state = {
            None: ProviderAccountState.AUTH_REQUIRED,
            ProviderHealthErrorCode.AUTH_NOT_CONFIGURED: ProviderAccountState.NOT_CONFIGURED,
            ProviderHealthErrorCode.SESSION_UNAVAILABLE: ProviderAccountState.DEGRADED,
            ProviderHealthErrorCode.SESSION_UNVERIFIED: ProviderAccountState.INVALID,
            ProviderHealthErrorCode.SUBSCRIPTION_REQUIRED: ProviderAccountState.INVALID,
            ProviderHealthErrorCode.RUNTIME_UNAVAILABLE: ProviderAccountState.RECOVERING,
            ProviderHealthErrorCode.CREDENTIAL_EXPIRED: ProviderAccountState.EXPIRED,
            ProviderHealthErrorCode.CREDENTIAL_INVALID: ProviderAccountState.INVALID,
            ProviderHealthErrorCode.CREDENTIAL_REVOKED: ProviderAccountState.REVOKED,
        }.get(health.error_code, ProviderAccountState.AUTH_REQUIRED)
    elif health.status is ProviderHealthStatus.UNAVAILABLE and not health.download_supported:
        state = ProviderAccountState.UNSUPPORTED
    elif health.status is ProviderHealthStatus.ERROR and code in {
        ProviderAccountErrorCode.RUNTIME_UNAVAILABLE,
        ProviderAccountErrorCode.REFRESH_FAILED,
    }:
        state = ProviderAccountState.RECOVERING
    elif health.status is ProviderHealthStatus.ERROR and code is not None:
        state = ProviderAccountState.DEGRADED
    if code is ProviderAccountErrorCode.CREDENTIAL_EXPIRED:
        state = ProviderAccountState.EXPIRED
    elif code is ProviderAccountErrorCode.CREDENTIAL_INVALID:
        state = ProviderAccountState.INVALID
    elif code is ProviderAccountErrorCode.CREDENTIAL_REVOKED:
        state = ProviderAccountState.REVOKED
    if state is ProviderAccountState.ERROR and code is None:
        code = ProviderAccountErrorCode.STATUS_CHECK_FAILED
    return ProviderAccountStatus(
        provider=health.provider,
        state=state,
        checked_at=utc_now(),
        authorization_methods=authorization_methods,
        error_code=code,
        disconnect_supported=False,
    )


def _normalize_error(code: ProviderHealthErrorCode | None) -> ProviderAccountErrorCode | None:
    if code is None:
        return None
    return {
        ProviderHealthErrorCode.AUTH_NOT_CONFIGURED: ProviderAccountErrorCode.AUTH_NOT_CONFIGURED,
        ProviderHealthErrorCode.SESSION_UNAVAILABLE: ProviderAccountErrorCode.SESSION_UNAVAILABLE,
        ProviderHealthErrorCode.SESSION_UNVERIFIED: ProviderAccountErrorCode.SESSION_UNVERIFIED,
        ProviderHealthErrorCode.SUBSCRIPTION_REQUIRED: (
            ProviderAccountErrorCode.SUBSCRIPTION_REQUIRED
        ),
        ProviderHealthErrorCode.RUNTIME_UNAVAILABLE: ProviderAccountErrorCode.RUNTIME_UNAVAILABLE,
        ProviderHealthErrorCode.PROVIDER_INITIALIZATION_FAILED: (
            ProviderAccountErrorCode.REFRESH_FAILED
        ),
        ProviderHealthErrorCode.HEALTH_CHECK_TIMEOUT: ProviderAccountErrorCode.STATUS_CHECK_FAILED,
        ProviderHealthErrorCode.UPSTREAM_ERROR: ProviderAccountErrorCode.STATUS_CHECK_FAILED,
        ProviderHealthErrorCode.CREDENTIAL_EXPIRED: ProviderAccountErrorCode.CREDENTIAL_EXPIRED,
        ProviderHealthErrorCode.CREDENTIAL_INVALID: ProviderAccountErrorCode.CREDENTIAL_INVALID,
        ProviderHealthErrorCode.CREDENTIAL_REVOKED: ProviderAccountErrorCode.CREDENTIAL_REVOKED,
    }[code]


def _failed_status(
    provider: MusicProviderName, code: ProviderAccountErrorCode
) -> ProviderAccountStatus:
    return ProviderAccountStatus(provider, ProviderAccountState.ERROR, utc_now(), error_code=code)


def _recovering_status(
    provider: MusicProviderName,
    authorization_methods: tuple[ProviderAuthorizationMethod, ...],
) -> ProviderAccountStatus:
    return ProviderAccountStatus(
        provider,
        ProviderAccountState.RECOVERING,
        utc_now(),
        authorization_methods=authorization_methods,
        error_code=ProviderAccountErrorCode.RUNTIME_UNAVAILABLE,
        disconnect_supported=True,
    )


def _spotify_overall_state(
    playback_state: ProviderAccountState,
    components: tuple[ProviderAccountComponentStatus, ...],
) -> ProviderAccountState:
    if playback_state is not ProviderAccountState.READY:
        return playback_state
    web_api = next(
        (item for item in components if item.component is ProviderAccountComponent.WEB_API), None
    )
    if web_api is None or web_api.state is ProviderAccountState.NOT_CONFIGURED:
        return playback_state
    if web_api.state is not ProviderAccountState.READY or (
        web_api.operational_state is not None
        and web_api.operational_state is not ProviderOperationalState.AVAILABLE
    ):
        return ProviderAccountState.DEGRADED
    return ProviderAccountState.READY
