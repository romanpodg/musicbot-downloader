"""Managed Apple Music session-token authorization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.enums import MusicProviderName
from app.core.provider_accounts import (
    AuthorizationCapabilities,
    ProviderAccountErrorCode,
    ProviderAccountState,
    ProviderAuthorizationOutcome,
    ProviderAuthorizationOutcomeStatus,
    ProviderSessionTokenInput,
    SensitiveValue,
)
from app.providers.account_management import ProviderAccountBackend


@dataclass(frozen=True, slots=True)
class AppleMusicAuthorizationResult:
    persisted: bool
    error_code: ProviderAccountErrorCode | None = None


class AppleMusicAuthorizationBoundary(Protocol):
    async def authorize_apple_music_session(
        self, token: SensitiveValue
    ) -> AppleMusicAuthorizationResult: ...


class AppleMusicAuthorizationDriver:
    capabilities = AuthorizationCapabilities(
        supports_configure=True,
        supports_validate=True,
        supports_authenticate=True,
        supports_session_validation=True,
        supports_entitlement_check=True,
        supports_readiness_projection=True,
    )

    def __init__(
        self, boundary: AppleMusicAuthorizationBoundary, account_backend: ProviderAccountBackend
    ) -> None:
        self._boundary = boundary
        self._account_backend = account_backend

    async def authorize_session_token(
        self, credentials: ProviderSessionTokenInput
    ) -> ProviderAuthorizationOutcome:
        if credentials.provider is not MusicProviderName.APPLE_MUSIC:
            return _failed(ProviderAccountErrorCode.AUTHORIZATION_UNSUPPORTED)
        try:
            result = await self._boundary.authorize_apple_music_session(credentials.token)
        except Exception:
            return _failed(ProviderAccountErrorCode.APPLE_MUSIC_AUTH_RUNTIME_UNAVAILABLE)
        if not result.persisted:
            return _failed(
                result.error_code or ProviderAccountErrorCode.APPLE_MUSIC_AUTH_SESSION_INVALID
            )
        try:
            await self._account_backend.reload_account_state()
            status = await self._account_backend.get_account_status(MusicProviderName.APPLE_MUSIC)
        except Exception:
            return _failed(ProviderAccountErrorCode.APPLE_MUSIC_AUTH_RELOAD_FAILED)
        if status.state is not ProviderAccountState.READY:
            return _failed(
                status.error_code or ProviderAccountErrorCode.APPLE_MUSIC_AUTH_RELOAD_FAILED
            )
        return ProviderAuthorizationOutcome(
            MusicProviderName.APPLE_MUSIC, ProviderAuthorizationOutcomeStatus.READY
        )


def _failed(code: ProviderAccountErrorCode) -> ProviderAuthorizationOutcome:
    return ProviderAuthorizationOutcome(
        MusicProviderName.APPLE_MUSIC, ProviderAuthorizationOutcomeStatus.FAILED, code
    )
