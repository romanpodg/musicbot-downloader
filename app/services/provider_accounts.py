"""OWNER-authorized provider-account management application service."""

from __future__ import annotations

import asyncio

from app.core.enums import MusicProviderName
from app.core.provider_accounts import (
    ProviderAccountErrorCode,
    ProviderAccountOverview,
    ProviderAccountState,
    ProviderAccountStatus,
    ProviderDisconnectOutcome,
    ProviderDisconnectOutcomeStatus,
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
        if await self._coordinator.is_active(provider):
            return ProviderAccountStatus(
                provider=provider,
                state=ProviderAccountState.AUTHORIZING,
                checked_at=status.checked_at,
                authorization_methods=status.authorization_methods,
                disconnect_supported=status.disconnect_supported,
            )
        return status


def _error_status(
    provider: MusicProviderName, code: ProviderAccountErrorCode
) -> ProviderAccountStatus:
    return ProviderAccountStatus(provider, ProviderAccountState.ERROR, utc_now(), error_code=code)
