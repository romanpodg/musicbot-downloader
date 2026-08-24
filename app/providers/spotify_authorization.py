"""Spotify playback pairing and Developer Web API credential drivers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.core.enums import MusicProviderName
from app.core.provider_accounts import (
    ProviderAccountComponent,
    ProviderAccountErrorCode,
    ProviderAccountState,
    ProviderAuthorizationChallenge,
    ProviderAuthorizationMethod,
    ProviderAuthorizationOutcome,
    ProviderAuthorizationOutcomeStatus,
    ProviderAuthorizationRequest,
    ProviderCompoundCredentialInput,
    ProviderLocalPairingChallenge,
    ProviderOperationalState,
    SensitiveValue,
)
from app.providers.account_management import ProviderAccountBackend


class SpotifyPlaybackPollStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    EXPIRED = "expired"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SpotifyPlaybackPairingStart:
    status: str
    flow_id: str | None = None
    advertised_host: str | None = None
    expires_in_seconds: float | None = None
    interval_seconds: float | None = None
    error_code: ProviderAccountErrorCode | None = None


@dataclass(frozen=True, slots=True)
class SpotifyPlaybackPairingPoll:
    status: SpotifyPlaybackPollStatus
    error_code: ProviderAccountErrorCode | None = None


@dataclass(frozen=True, slots=True)
class SpotifyWebApiAuthorizationResult:
    persisted: bool
    operational_state: ProviderOperationalState = ProviderOperationalState.UNKNOWN
    error_code: ProviderAccountErrorCode | None = None


class SpotifyAuthorizationBoundary(Protocol):
    async def start_spotify_playback_pairing(self) -> SpotifyPlaybackPairingStart: ...

    async def poll_spotify_playback_pairing(self, flow_id: str) -> SpotifyPlaybackPairingPoll: ...

    async def cancel_spotify_playback_pairing(self, flow_id: str) -> None: ...

    async def authorize_spotify_webapi_credentials(
        self, client_id: SensitiveValue, client_secret: SensitiveValue
    ) -> SpotifyWebApiAuthorizationResult: ...


class SpotifyAuthorizationDriverError(Exception):
    def __init__(self, code: ProviderAccountErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


class SpotifyPlaybackAuthorizationDriver:
    """Poll a child-owned Zeroconf generation without retaining the child RPC lock."""

    def __init__(
        self,
        boundary: SpotifyAuthorizationBoundary,
        account_backend: ProviderAccountBackend,
    ) -> None:
        self._boundary = boundary
        self._account_backend = account_backend

    async def start(self, request: ProviderAuthorizationRequest) -> ProviderLocalPairingChallenge:
        if (
            request.provider is not MusicProviderName.SPOTIFY
            or request.method is not ProviderAuthorizationMethod.BROWSER_DEVICE_LINK
        ):
            raise SpotifyAuthorizationDriverError(
                ProviderAccountErrorCode.AUTHORIZATION_UNSUPPORTED
            )
        try:
            started = await self._boundary.start_spotify_playback_pairing()
        except Exception:
            raise SpotifyAuthorizationDriverError(
                ProviderAccountErrorCode.SPOTIFY_PLAYBACK_START_FAILED
            ) from None
        if started.status != "started":
            raise SpotifyAuthorizationDriverError(
                started.error_code or ProviderAccountErrorCode.SPOTIFY_PLAYBACK_START_FAILED
            )
        if (
            started.flow_id is None
            or started.advertised_host is None
            or started.expires_in_seconds is None
            or started.interval_seconds is None
            or started.expires_in_seconds <= 0
            or started.interval_seconds <= 0
        ):
            raise SpotifyAuthorizationDriverError(
                ProviderAccountErrorCode.SPOTIFY_PLAYBACK_START_FAILED
            )
        from datetime import UTC, datetime, timedelta

        return ProviderLocalPairingChallenge(
            provider=MusicProviderName.SPOTIFY,
            flow_id=started.flow_id,
            expires_at=datetime.now(UTC) + timedelta(seconds=started.expires_in_seconds),
            polling_interval_seconds=started.interval_seconds,
            advertised_host=started.advertised_host,
        )

    async def wait(
        self, challenge: ProviderLocalPairingChallenge | ProviderAuthorizationChallenge
    ) -> ProviderAuthorizationOutcome:
        from datetime import UTC, datetime

        if not isinstance(challenge, ProviderLocalPairingChallenge):
            return _playback_failed(ProviderAccountErrorCode.AUTHORIZATION_UNSUPPORTED)
        cancelled = False
        try:
            while datetime.now(UTC) < challenge.expires_at:
                await asyncio.sleep(challenge.polling_interval_seconds)
                try:
                    poll = await self._boundary.poll_spotify_playback_pairing(challenge.flow_id)
                except Exception:
                    return _playback_failed(ProviderAccountErrorCode.SPOTIFY_PLAYBACK_START_FAILED)
                if poll.status is SpotifyPlaybackPollStatus.PENDING:
                    continue
                if poll.status is SpotifyPlaybackPollStatus.APPROVED:
                    return await self._reload_and_verify()
                if poll.status is SpotifyPlaybackPollStatus.EXPIRED:
                    return _playback_failed(
                        poll.error_code or ProviderAccountErrorCode.SPOTIFY_PLAYBACK_CANCELLED
                    )
                return _playback_failed(
                    poll.error_code or ProviderAccountErrorCode.SPOTIFY_PLAYBACK_START_FAILED
                )
            return _playback_failed(ProviderAccountErrorCode.SPOTIFY_PLAYBACK_CANCELLED)
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            if not cancelled:
                try:
                    await self._boundary.cancel_spotify_playback_pairing(challenge.flow_id)
                except Exception:
                    pass

    async def cancel(self, flow_id: str) -> None:
        try:
            await self._boundary.cancel_spotify_playback_pairing(flow_id)
        except Exception:
            pass

    async def _reload_and_verify(self) -> ProviderAuthorizationOutcome:
        try:
            await self._account_backend.reload_account_state()
            status = await self._account_backend.get_account_status(MusicProviderName.SPOTIFY)
        except Exception:
            return _playback_failed(ProviderAccountErrorCode.SPOTIFY_PLAYBACK_RELOAD_FAILED)
        playback = status.component_status(ProviderAccountComponent.PLAYBACK)
        if playback is None or playback.state is not ProviderAccountState.READY:
            return _playback_failed(ProviderAccountErrorCode.SPOTIFY_PLAYBACK_RELOAD_FAILED)
        return ProviderAuthorizationOutcome(
            MusicProviderName.SPOTIFY, ProviderAuthorizationOutcomeStatus.READY
        )


class SpotifyWebApiAuthorizationDriver:
    """Validate and atomically persist a child-isolated Spotify Developer pair."""

    def __init__(
        self,
        boundary: SpotifyAuthorizationBoundary,
        account_backend: ProviderAccountBackend,
    ) -> None:
        self._boundary = boundary
        self._account_backend = account_backend

    async def authorize_credentials(
        self, credentials: ProviderCompoundCredentialInput
    ) -> ProviderAuthorizationOutcome:
        if credentials.provider is not MusicProviderName.SPOTIFY:
            return _webapi_failed(ProviderAccountErrorCode.AUTHORIZATION_UNSUPPORTED)
        try:
            result = await self._boundary.authorize_spotify_webapi_credentials(
                credentials.client_id, credentials.client_secret
            )
        except Exception:
            return _webapi_failed(ProviderAccountErrorCode.SPOTIFY_WEBAPI_NETWORK_ERROR)
        if not result.persisted:
            return _webapi_failed(
                result.error_code or ProviderAccountErrorCode.SPOTIFY_WEBAPI_INVALID_RESPONSE
            )
        try:
            status = await self._account_backend.get_account_status(MusicProviderName.SPOTIFY)
        except Exception:
            return _webapi_failed(ProviderAccountErrorCode.SPOTIFY_WEBAPI_INVALID_RESPONSE)
        web_api = status.component_status(ProviderAccountComponent.WEB_API)
        if web_api is None or web_api.state is not ProviderAccountState.READY:
            return _webapi_failed(ProviderAccountErrorCode.SPOTIFY_WEBAPI_INVALID_RESPONSE)
        return ProviderAuthorizationOutcome(
            MusicProviderName.SPOTIFY, ProviderAuthorizationOutcomeStatus.READY
        )


def _playback_failed(code: ProviderAccountErrorCode) -> ProviderAuthorizationOutcome:
    return ProviderAuthorizationOutcome(
        MusicProviderName.SPOTIFY, ProviderAuthorizationOutcomeStatus.FAILED, code
    )


def _webapi_failed(code: ProviderAccountErrorCode) -> ProviderAuthorizationOutcome:
    return ProviderAuthorizationOutcome(
        MusicProviderName.SPOTIFY, ProviderAuthorizationOutcomeStatus.FAILED, code
    )
