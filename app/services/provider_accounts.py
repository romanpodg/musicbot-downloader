"""OWNER-authorized provider-account management application service."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from app.core.enums import MusicProviderName
from app.core.provider_accounts import (
    ProviderAccountComponent,
    ProviderAccountErrorCode,
    ProviderAccountOverview,
    ProviderAccountState,
    ProviderAccountStatus,
    ProviderAuthorizationMethod,
    ProviderAuthorizationOutcome,
    ProviderAuthorizationRequest,
    ProviderAuthorizationStartOutcome,
    ProviderAuthorizationStartStatus,
    ProviderCompoundCredentialInput,
    ProviderDisconnectOutcome,
    ProviderDisconnectOutcomeStatus,
    ProviderSecretInput,
    ProviderSensitiveInputChallenge,
    SensitiveValue,
)
from app.providers.account_management import (
    MANAGED_PROVIDER_ORDER,
    ProviderAccountBackend,
    ProviderAccountBackendError,
)
from app.services.authorization import (
    AdminAccessContext,
    AdminPermission,
    TelegramAuthorizationService,
)
from app.services.provider_authorization import ProviderAuthorizationCoordinator
from app.storage.models.base import utc_now


class ProviderAccountManagementService:
    def __init__(
        self,
        backend: ProviderAccountBackend,
        authorization: TelegramAuthorizationService,
        coordinator: ProviderAuthorizationCoordinator,
    ) -> None:
        self._backend = backend
        self._authorization = authorization
        self._coordinator = coordinator

    async def authorize(self, actor_user_id: int) -> AdminAccessContext:
        return await self._authorization.require_permission(
            actor_user_id, AdminPermission.PROVIDER_ACCOUNTS_MANAGE
        )

    async def get_overview(self, actor_user_id: int) -> ProviderAccountOverview:
        await self.authorize(actor_user_id)
        return await self._overview()

    async def get_status(
        self, actor_user_id: int, provider: MusicProviderName
    ) -> ProviderAccountStatus:
        await self.authorize(actor_user_id)
        return await self._status(provider)

    async def refresh(self, actor_user_id: int) -> ProviderAccountOverview:
        await self.authorize(actor_user_id)
        try:
            await self._backend.reload_account_state()
        except ProviderAccountBackendError as exc:
            return ProviderAccountOverview(
                utc_now(),
                tuple(_error_status(provider, exc.code) for provider in MANAGED_PROVIDER_ORDER),
            )
        except Exception:
            return ProviderAccountOverview(
                utc_now(),
                tuple(
                    _error_status(provider, ProviderAccountErrorCode.REFRESH_FAILED)
                    for provider in MANAGED_PROVIDER_ORDER
                ),
            )
        return await self._overview()

    async def refresh_status(
        self, actor_user_id: int, provider: MusicProviderName
    ) -> ProviderAccountStatus:
        await self.authorize(actor_user_id)
        try:
            await self._backend.reload_account_state()
        except Exception:
            return _error_status(provider, ProviderAccountErrorCode.REFRESH_FAILED)
        return await self._status(provider)

    async def disconnect(
        self, actor_user_id: int, provider: MusicProviderName
    ) -> ProviderDisconnectOutcome:
        await self.authorize(actor_user_id)
        try:
            return await self._backend.disconnect_account(provider)
        except Exception:
            return ProviderDisconnectOutcome(
                provider,
                ProviderDisconnectOutcomeStatus.FAILED,
                ProviderAccountErrorCode.DISCONNECT_FAILED,
            )

    async def start_authorization(
        self,
        actor_user_id: int,
        provider: MusicProviderName,
        method: ProviderAuthorizationMethod | None = None,
    ) -> ProviderAuthorizationStartOutcome:
        await self.authorize(actor_user_id)
        if method is None:
            methods = self._coordinator.available_methods(provider)
            method = (
                ProviderAuthorizationMethod.BROWSER_DEVICE_LINK
                if ProviderAuthorizationMethod.BROWSER_DEVICE_LINK in methods
                else methods[0]
                if len(methods) == 1
                else ProviderAuthorizationMethod.BROWSER_DEVICE_LINK
            )
        request = ProviderAuthorizationRequest(provider, method)
        if await self._coordinator.is_active(provider):
            return await self._coordinator.start(request)
        status = await self._status(provider)
        playback = status.component_status(ProviderAccountComponent.PLAYBACK)
        if (
            method is ProviderAuthorizationMethod.BROWSER_DEVICE_LINK
            and provider is MusicProviderName.SPOTIFY
            and playback is not None
            and playback.state is ProviderAccountState.READY
        ) or (
            method is not ProviderAuthorizationMethod.COMPOUND_CREDENTIALS
            and provider is not MusicProviderName.SPOTIFY
            and status.state is ProviderAccountState.READY
        ):
            return ProviderAuthorizationStartOutcome(
                provider, ProviderAuthorizationStartStatus.ALREADY_READY
            )
        return await self._coordinator.start(request)

    async def pending_sensitive_challenge(
        self,
        actor_user_id: int,
        provider: MusicProviderName,
        method: ProviderAuthorizationMethod | None = None,
    ) -> ProviderSensitiveInputChallenge | None:
        await self.authorize(actor_user_id)
        return await self._coordinator.pending_sensitive_challenge(provider, method)

    async def submit_sensitive_secret(
        self,
        actor_user_id: int,
        provider: MusicProviderName,
        flow_id: str,
        secret: SensitiveValue,
    ) -> ProviderAuthorizationOutcome:
        await self.authorize(actor_user_id)
        return await self._coordinator.submit_sensitive_secret(
            provider, flow_id, ProviderSecretInput(provider, secret)
        )

    async def submit_compound_credentials(
        self,
        actor_user_id: int,
        provider: MusicProviderName,
        flow_id: str,
        client_id: SensitiveValue,
        client_secret: SensitiveValue,
    ) -> ProviderAuthorizationOutcome:
        await self.authorize(actor_user_id)
        return await self._coordinator.submit_compound_credentials(
            provider,
            flow_id,
            ProviderCompoundCredentialInput(provider, client_id, client_secret),
        )

    async def fail_sensitive_input(
        self,
        actor_user_id: int,
        provider: MusicProviderName,
        flow_id: str,
        code: ProviderAccountErrorCode,
    ) -> ProviderAuthorizationOutcome:
        await self.authorize(actor_user_id)
        return await self._coordinator.fail_sensitive_input(provider, flow_id, code)

    async def wait_authorization(
        self, actor_user_id: int, provider: MusicProviderName, flow_id: str
    ) -> ProviderAuthorizationOutcome:
        await self.authorize(actor_user_id)
        return await self._coordinator.wait(provider, flow_id)

    async def cancel_authorization(
        self, actor_user_id: int, provider: MusicProviderName, flow_id: str
    ) -> ProviderAuthorizationOutcome:
        await self.authorize(actor_user_id)
        return await self._coordinator.cancel(provider, flow_id)

    async def _overview(self) -> ProviderAccountOverview:
        accounts = await asyncio.gather(
            *(self._status(provider) for provider in MANAGED_PROVIDER_ORDER)
        )
        return ProviderAccountOverview(utc_now(), tuple(accounts))

    async def _status(self, provider: MusicProviderName) -> ProviderAccountStatus:
        try:
            status = await self._backend.get_account_status(provider)
        except Exception:
            return _error_status(provider, ProviderAccountErrorCode.STATUS_CHECK_FAILED)
        if status.provider is not provider:
            return _error_status(provider, ProviderAccountErrorCode.INVALID_BACKEND_RESPONSE)
        active_method = await self._coordinator.active_method(provider)
        if active_method is not None:
            if provider is MusicProviderName.SPOTIFY and status.components:
                target = (
                    ProviderAccountComponent.PLAYBACK
                    if active_method is ProviderAuthorizationMethod.BROWSER_DEVICE_LINK
                    else ProviderAccountComponent.WEB_API
                )
                components = tuple(
                    replace(component, state=ProviderAccountState.AUTHORIZING, error_code=None)
                    if component.component is target
                    else component
                    for component in status.components
                )
                return replace(
                    status,
                    state=(
                        ProviderAccountState.AUTHORIZING
                        if target is ProviderAccountComponent.PLAYBACK
                        else status.state
                    ),
                    components=components,
                )
            return ProviderAccountStatus(
                provider=provider,
                state=ProviderAccountState.AUTHORIZING,
                checked_at=status.checked_at,
                authorization_methods=status.authorization_methods,
                disconnect_supported=status.disconnect_supported,
                components=status.components,
            )
        return status


def _error_status(
    provider: MusicProviderName, code: ProviderAccountErrorCode
) -> ProviderAccountStatus:
    return ProviderAccountStatus(provider, ProviderAccountState.ERROR, utc_now(), error_code=code)
