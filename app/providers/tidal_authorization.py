"""Sanitized Tidal device-flow driver over the isolated provider child boundary."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from app.core.enums import MusicProviderName
from app.core.provider_accounts import (
    ProviderAccountErrorCode,
    ProviderAccountState,
    ProviderAuthorizationChallenge,
    ProviderAuthorizationMethod,
    ProviderAuthorizationOutcome,
    ProviderAuthorizationOutcomeStatus,
    ProviderAuthorizationRequest,
    ProviderLocalPairingChallenge,
)
from app.providers.account_management import ProviderAccountBackend


class TidalDevicePollStatus(StrEnum):
    PENDING = "pending"
    SLOW_DOWN = "slow_down"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    NETWORK_ERROR = "network_error"
    INVALID_RESPONSE = "invalid_response"
    PERSIST_FAILED = "persist_failed"


@dataclass(frozen=True, slots=True)
class TidalDeviceAuthorizationStart:
    status: str
    flow_id: str | None = None
    verification_url: str | None = None
    expires_in_seconds: float | None = None
    interval_seconds: float | None = None
    error_code: ProviderAccountErrorCode | None = None


@dataclass(frozen=True, slots=True)
class TidalDeviceAuthorizationPoll:
    status: TidalDevicePollStatus
    retry_after_seconds: float | None = None
    error_code: ProviderAccountErrorCode | None = None


class TidalDeviceAuthorizationBoundary(Protocol):
    async def start_tidal_device_authorization(self) -> TidalDeviceAuthorizationStart: ...

    async def poll_tidal_device_authorization(
        self, flow_id: str
    ) -> TidalDeviceAuthorizationPoll: ...

    async def cancel_tidal_device_authorization(self, flow_id: str) -> None: ...


class TidalAuthorizationDriverError(Exception):
    def __init__(self, code: ProviderAccountErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


class TidalDeviceAuthorizationDriver:
    """Poll one bounded child operation at a time and verify runtime truth on success."""

    def __init__(
        self,
        boundary: TidalDeviceAuthorizationBoundary,
        account_backend: ProviderAccountBackend,
        *,
        max_transient_failures: int = 3,
    ) -> None:
        self._boundary = boundary
        self._account_backend = account_backend
        self._max_transient_failures = max_transient_failures

    async def start(self, request: ProviderAuthorizationRequest) -> ProviderAuthorizationChallenge:
        if (
            request.provider is not MusicProviderName.TIDAL
            or request.method is not ProviderAuthorizationMethod.BROWSER_DEVICE_LINK
        ):
            raise TidalAuthorizationDriverError(ProviderAccountErrorCode.AUTHORIZATION_UNSUPPORTED)
        try:
            started = await self._boundary.start_tidal_device_authorization()
        except Exception:
            raise TidalAuthorizationDriverError(
                ProviderAccountErrorCode.TIDAL_AUTH_START_FAILED
            ) from None
        if started.status != "started":
            raise TidalAuthorizationDriverError(
                started.error_code or ProviderAccountErrorCode.TIDAL_AUTH_START_FAILED
            )
        if (
            started.flow_id is None
            or started.verification_url is None
            or started.expires_in_seconds is None
            or started.interval_seconds is None
            or started.expires_in_seconds <= 0
            or started.interval_seconds <= 0
        ):
            raise TidalAuthorizationDriverError(
                ProviderAccountErrorCode.TIDAL_AUTH_INVALID_RESPONSE
            )
        return ProviderAuthorizationChallenge(
            provider=MusicProviderName.TIDAL,
            flow_id=started.flow_id,
            verification_url=started.verification_url,
            expires_at=datetime.now(UTC) + timedelta(seconds=started.expires_in_seconds),
            polling_interval_seconds=started.interval_seconds,
        )

    async def wait(
        self, challenge: ProviderAuthorizationChallenge | ProviderLocalPairingChallenge
    ) -> ProviderAuthorizationOutcome:
        if not isinstance(challenge, ProviderAuthorizationChallenge):
            return _failed(ProviderAccountErrorCode.AUTHORIZATION_UNSUPPORTED)
        delay = challenge.polling_interval_seconds
        transient_failures = 0
        cancelled = False
        try:
            while datetime.now(UTC) < challenge.expires_at:
                await asyncio.sleep(delay)
                try:
                    poll = await self._boundary.poll_tidal_device_authorization(challenge.flow_id)
                except Exception:
                    transient_failures += 1
                    if transient_failures >= self._max_transient_failures:
                        return _failed(ProviderAccountErrorCode.TIDAL_AUTH_NETWORK_ERROR)
                    continue
                if poll.status is TidalDevicePollStatus.PENDING:
                    transient_failures = 0
                    delay = poll.retry_after_seconds or challenge.polling_interval_seconds
                    continue
                if poll.status is TidalDevicePollStatus.SLOW_DOWN:
                    transient_failures = 0
                    delay = poll.retry_after_seconds or (delay + 5)
                    continue
                if poll.status is TidalDevicePollStatus.NETWORK_ERROR:
                    transient_failures += 1
                    if transient_failures >= self._max_transient_failures:
                        return _failed(ProviderAccountErrorCode.TIDAL_AUTH_NETWORK_ERROR)
                    delay = poll.retry_after_seconds or delay
                    continue
                if poll.status is TidalDevicePollStatus.APPROVED:
                    return await self._reload_and_verify()
                if poll.status is TidalDevicePollStatus.DENIED:
                    return _failed(ProviderAccountErrorCode.TIDAL_AUTH_DENIED)
                if poll.status is TidalDevicePollStatus.EXPIRED:
                    return _failed(ProviderAccountErrorCode.TIDAL_AUTH_EXPIRED)
                if poll.status is TidalDevicePollStatus.PERSIST_FAILED:
                    return _failed(ProviderAccountErrorCode.TIDAL_AUTH_PERSIST_FAILED)
                return _failed(ProviderAccountErrorCode.TIDAL_AUTH_INVALID_RESPONSE)
            return _failed(ProviderAccountErrorCode.TIDAL_AUTH_EXPIRED)
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            if not cancelled:
                try:
                    await self._boundary.cancel_tidal_device_authorization(challenge.flow_id)
                except Exception:
                    pass

    async def cancel(self, flow_id: str) -> None:
        try:
            await self._boundary.cancel_tidal_device_authorization(flow_id)
        except Exception:
            pass

    async def _reload_and_verify(self) -> ProviderAuthorizationOutcome:
        try:
            await self._account_backend.reload_account_state()
            status = await self._account_backend.get_account_status(MusicProviderName.TIDAL)
        except Exception:
            return _failed(ProviderAccountErrorCode.TIDAL_AUTH_RELOAD_FAILED)
        if status.state is not ProviderAccountState.READY:
            return _failed(ProviderAccountErrorCode.TIDAL_AUTH_RELOAD_FAILED)
        return ProviderAuthorizationOutcome(
            MusicProviderName.TIDAL, ProviderAuthorizationOutcomeStatus.READY
        )


def _failed(code: ProviderAccountErrorCode) -> ProviderAuthorizationOutcome:
    return ProviderAuthorizationOutcome(
        MusicProviderName.TIDAL, ProviderAuthorizationOutcomeStatus.FAILED, code
    )
