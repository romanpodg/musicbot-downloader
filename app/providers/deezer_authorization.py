"""Secure Deezer ARL authorization over the isolated provider-child boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.enums import MusicProviderName
from app.core.provider_accounts import (
    ProviderAccountErrorCode,
    ProviderAccountState,
    ProviderAuthorizationOutcome,
    ProviderAuthorizationOutcomeStatus,
    ProviderSecretInput,
    SensitiveValue,
)
from app.providers.account_management import ProviderAccountBackend


@dataclass(frozen=True, slots=True)
class DeezerArlAuthorizationResult:
    """Strict child result; contains no profile, cookie, token, or account data."""

    persisted: bool
    error_code: ProviderAccountErrorCode | None = None


class DeezerArlAuthorizationBoundary(Protocol):
    async def authorize_deezer_arl(
        self, credential: SensitiveValue
    ) -> DeezerArlAuthorizationResult: ...


class DeezerArlAuthorizationDriver:
    """Persist only child-validated credentials, then reload and verify runtime truth."""

    def __init__(
        self,
        boundary: DeezerArlAuthorizationBoundary,
        account_backend: ProviderAccountBackend,
    ) -> None:
        self._boundary = boundary
        self._account_backend = account_backend

    async def authorize_secret(
        self, credential: ProviderSecretInput
    ) -> ProviderAuthorizationOutcome:
        if credential.provider is not MusicProviderName.DEEZER:
            return _failed(ProviderAccountErrorCode.AUTHORIZATION_UNSUPPORTED)
        try:
            persisted = await self._boundary.authorize_deezer_arl(credential.secret)
        except Exception:
            return _failed(ProviderAccountErrorCode.DEEZER_AUTH_NETWORK_ERROR)
        if not persisted.persisted:
            return _failed(
                persisted.error_code or ProviderAccountErrorCode.DEEZER_AUTH_INVALID_RESPONSE
            )
        try:
            await self._account_backend.reload_account_state()
            status = await self._account_backend.get_account_status(MusicProviderName.DEEZER)
        except Exception:
            return _failed(ProviderAccountErrorCode.DEEZER_AUTH_RELOAD_FAILED)
        if status.state is not ProviderAccountState.READY:
            return _failed(ProviderAccountErrorCode.DEEZER_AUTH_RELOAD_FAILED)
        return ProviderAuthorizationOutcome(
            MusicProviderName.DEEZER, ProviderAuthorizationOutcomeStatus.READY
        )


def _failed(code: ProviderAccountErrorCode) -> ProviderAuthorizationOutcome:
    return ProviderAuthorizationOutcome(
        MusicProviderName.DEEZER, ProviderAuthorizationOutcomeStatus.FAILED, code
    )
