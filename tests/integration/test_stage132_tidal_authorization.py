from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.core.enums import MusicProviderName, UserRole
from app.core.provider_accounts import (
    ProviderAccountState,
    ProviderAccountStatus,
    ProviderAuthorizationChallenge,
    ProviderAuthorizationMethod,
    ProviderAuthorizationOutcome,
    ProviderAuthorizationOutcomeStatus,
    ProviderAuthorizationRequest,
    ProviderAuthorizationStartStatus,
    ProviderDisconnectOutcome,
    ProviderDisconnectOutcomeStatus,
)
from app.services.authorization import AuthorizationError, TelegramAuthorizationService
from app.services.provider_accounts import ProviderAccountManagementService
from app.services.provider_authorization import ProviderAuthorizationCoordinator
from app.storage import Database


class AccountBackend:
    def __init__(self, state: ProviderAccountState = ProviderAccountState.NOT_CONFIGURED) -> None:
        self.state = state
        self.status_calls = 0

    async def get_account_status(self, provider: MusicProviderName) -> ProviderAccountStatus:
        self.status_calls += 1
        methods = (
            (ProviderAuthorizationMethod.BROWSER_DEVICE_LINK,)
            if provider is MusicProviderName.TIDAL
            else ()
        )
        return ProviderAccountStatus(
            provider, self.state, datetime.now(UTC), authorization_methods=methods
        )

    async def reload_account_state(self) -> None:
        return None

    async def disconnect_account(self, provider: MusicProviderName) -> ProviderDisconnectOutcome:
        return ProviderDisconnectOutcome(provider, ProviderDisconnectOutcomeStatus.UNSUPPORTED)


class BlockingDriver:
    def __init__(self) -> None:
        self.started = 0
        self.release = asyncio.Event()
        self.cancelled: list[str] = []

    async def start(self, request: ProviderAuthorizationRequest) -> ProviderAuthorizationChallenge:
        self.started += 1
        return ProviderAuthorizationChallenge(
            request.provider,
            f"{self.started:016x}",
            "https://login.tidal.com/device",
            datetime.now(UTC) + timedelta(minutes=1),
            1,
        )

    async def wait(self, challenge: ProviderAuthorizationChallenge) -> ProviderAuthorizationOutcome:
        await self.release.wait()
        return ProviderAuthorizationOutcome(
            challenge.provider, ProviderAuthorizationOutcomeStatus.READY
        )

    async def cancel(self, flow_id: str) -> None:
        self.cancelled.append(flow_id)


async def _user(database: Database, telegram_id: int, role: UserRole) -> int:
    async with database.transaction() as repositories:
        return (
            await repositories.users.create_user(telegram_id, first_name="Stage 13.2", role=role)
        ).id


async def test_only_authoritative_owner_can_start_real_tidal_method(database: Database) -> None:
    owner = await _user(database, 132201, UserRole.OWNER)
    admin = await _user(database, 132202, UserRole.ADMIN)
    user = await _user(database, 132203, UserRole.USER)
    stale_owner = await _user(database, 132204, UserRole.OWNER)
    driver = BlockingDriver()
    coordinator = ProviderAuthorizationCoordinator(
        {
            (
                MusicProviderName.TIDAL,
                ProviderAuthorizationMethod.BROWSER_DEVICE_LINK,
            ): driver
        }
    )
    service = ProviderAccountManagementService(
        AccountBackend(),
        TelegramAuthorizationService(database, owner_id=132201),
        coordinator,
    )

    for denied in (admin, user, stale_owner):
        with pytest.raises(AuthorizationError):
            await service.start_authorization(denied, MusicProviderName.TIDAL)
    started = await service.start_authorization(owner, MusicProviderName.TIDAL)
    conflict = await service.start_authorization(owner, MusicProviderName.TIDAL)

    assert started.status is ProviderAuthorizationStartStatus.STARTED
    assert started.challenge is not None
    assert conflict.status is ProviderAuthorizationStartStatus.ALREADY_ACTIVE
    assert driver.started == 1
    await service.cancel_authorization(owner, MusicProviderName.TIDAL, started.challenge.flow_id)
    await coordinator.close()


async def test_spotify_and_deezer_authorization_remain_unsupported(database: Database) -> None:
    owner = await _user(database, 132211, UserRole.OWNER)
    coordinator = ProviderAuthorizationCoordinator()
    service = ProviderAccountManagementService(
        AccountBackend(),
        TelegramAuthorizationService(database, owner_id=132211),
        coordinator,
    )

    for provider in (MusicProviderName.SPOTIFY, MusicProviderName.DEEZER):
        result = await service.start_authorization(owner, provider)
        assert result.status is ProviderAuthorizationStartStatus.UNSUPPORTED


async def test_ready_tidal_account_does_not_start_replacement_flow(database: Database) -> None:
    owner = await _user(database, 132221, UserRole.OWNER)
    driver = BlockingDriver()
    coordinator = ProviderAuthorizationCoordinator(
        {
            (
                MusicProviderName.TIDAL,
                ProviderAuthorizationMethod.BROWSER_DEVICE_LINK,
            ): driver
        }
    )
    service = ProviderAccountManagementService(
        AccountBackend(ProviderAccountState.READY),
        TelegramAuthorizationService(database, owner_id=132221),
        coordinator,
    )

    result = await service.start_authorization(owner, MusicProviderName.TIDAL)

    assert result.status is ProviderAuthorizationStartStatus.ALREADY_READY
    assert driver.started == 0
