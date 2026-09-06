"""Managed Qobuz email/password authorization over the isolated runtime boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.enums import MusicProviderName
from app.core.provider_accounts import (
    ProviderAccountErrorCode,
    ProviderAccountState,
    ProviderAuthorizationOutcome,
    ProviderAuthorizationOutcomeStatus,
    ProviderCompoundCredentialInput,
    ProviderQobuzCredentialInput,
    SensitiveValue,
)
from app.providers.account_management import ProviderAccountBackend


@dataclass(frozen=True, slots=True)
class QobuzAuthorizationResult:
    persisted: bool
    error_code: ProviderAccountErrorCode | None = None


class QobuzAuthorizationBoundary(Protocol):
    async def authorize_qobuz_credentials(
        self, email: SensitiveValue, password: SensitiveValue
    ) -> QobuzAuthorizationResult: ...


class QobuzAuthorizationDriver:
    """Child validates and owns credentials; readiness is reloaded and verified."""

    def __init__(
        self, boundary: QobuzAuthorizationBoundary, account_backend: ProviderAccountBackend
    ) -> None:
        self._boundary = boundary
        self._account_backend = account_backend

    async def authorize_qobuz_credentials(
        self, credentials: ProviderQobuzCredentialInput
    ) -> ProviderAuthorizationOutcome:
        if credentials.provider is not MusicProviderName.QOBUZ:
            return _failed(ProviderAccountErrorCode.AUTHORIZATION_UNSUPPORTED)
        try:
            result = await self._boundary.authorize_qobuz_credentials(
                credentials.email, credentials.password
            )
        except Exception:
            return _failed(ProviderAccountErrorCode.QOBUZ_AUTH_NETWORK_ERROR)
        if not result.persisted:
            return _failed(
                result.error_code or ProviderAccountErrorCode.QOBUZ_AUTH_INVALID_CREDENTIALS
            )
        try:
            await self._account_backend.reload_account_state()
            status = await self._account_backend.get_account_status(MusicProviderName.QOBUZ)
        except Exception:
            return _failed(ProviderAccountErrorCode.QOBUZ_AUTH_RELOAD_FAILED)
        if status.state is not ProviderAccountState.READY:
            return _failed(status.error_code or ProviderAccountErrorCode.QOBUZ_AUTH_RELOAD_FAILED)
        return ProviderAuthorizationOutcome(
            MusicProviderName.QOBUZ, ProviderAuthorizationOutcomeStatus.READY
        )

    async def authorize_credentials(
        self, credentials: ProviderCompoundCredentialInput
    ) -> ProviderAuthorizationOutcome:
        """Compatibility bridge for the generic compound submission path."""
        return await self.authorize_qobuz_credentials(
            ProviderQobuzCredentialInput(
                credentials.provider, credentials.client_id, credentials.client_secret
            )
        )


def _failed(code: ProviderAccountErrorCode) -> ProviderAuthorizationOutcome:
    return ProviderAuthorizationOutcome(
        MusicProviderName.QOBUZ, ProviderAuthorizationOutcomeStatus.FAILED, code
    )
