from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot, Dispatcher
from aiogram.enums import ChatType
from aiogram.methods import DeleteMessage, EditMessageText, SendMessage
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser

from app.core.enums import MusicProviderName, UserRole
from app.core.provider_accounts import (
    ProviderAccountComponent,
    ProviderAccountComponentStatus,
    ProviderAccountErrorCode,
    ProviderAccountState,
    ProviderAccountStatus,
    ProviderAuthorizationMethod,
    ProviderAuthorizationOutcome,
    ProviderAuthorizationOutcomeStatus,
    ProviderAuthorizationStartStatus,
    ProviderCompoundCredentialInput,
    ProviderDisconnectOutcome,
    ProviderDisconnectOutcomeStatus,
    ProviderLocalPairingChallenge,
    ProviderOperationalState,
)
from app.i18n import LocalizationService
from app.services.admin_management import AdministratorManagementService
from app.services.admin_overview import AdminOverviewService
from app.services.authorization import AuthorizationError, TelegramAuthorizationService
from app.services.provider_accounts import ProviderAccountManagementService
from app.services.provider_authorization import ProviderAuthorizationCoordinator
from app.services.telegram_users import TelegramUserService
from app.storage import Database
from app.telegram.admin_handlers import AdminHandlerDependencies, create_admin_router
from app.telegram.admin_management_presentation import AdminManagementPresentation
from app.telegram.admin_presentation import AdminPresentation
from app.telegram.provider_accounts_presentation import ProviderAccountsPresentation

CLIENT_ID = "stage134-telegram-client-id-distinctive"
CLIENT_SECRET = "stage134-telegram-client-secret-distinctive"


class AccountBackend:
    async def get_account_status(self, provider: MusicProviderName) -> ProviderAccountStatus:
        components = (
            ProviderAccountComponentStatus(
                ProviderAccountComponent.PLAYBACK, ProviderAccountState.AUTH_REQUIRED
            ),
            ProviderAccountComponentStatus(
                ProviderAccountComponent.WEB_API,
                ProviderAccountState.NOT_CONFIGURED,
                operational_state=ProviderOperationalState.UNKNOWN,
            ),
        )
        return ProviderAccountStatus(
            provider,
            ProviderAccountState.AUTH_REQUIRED,
            datetime.now(UTC),
            (
                ProviderAuthorizationMethod.BROWSER_DEVICE_LINK,
                ProviderAuthorizationMethod.COMPOUND_CREDENTIALS,
            ),
            components=components if provider is MusicProviderName.SPOTIFY else (),
        )

    async def reload_account_state(self) -> None:
        return None

    async def disconnect_account(self, provider: MusicProviderName) -> ProviderDisconnectOutcome:
        return ProviderDisconnectOutcome(provider, ProviderDisconnectOutcomeStatus.UNSUPPORTED)


class CaptureDriver:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls = 0

    async def authorize_credentials(
        self, credentials: ProviderCompoundCredentialInput
    ) -> ProviderAuthorizationOutcome:
        self.events.append("backend")
        self.calls += 1
        assert credentials.client_id.reveal_to_provider_backend() == CLIENT_ID
        assert credentials.client_secret.reveal_to_provider_backend() == CLIENT_SECRET
        return ProviderAuthorizationOutcome(
            MusicProviderName.SPOTIFY,
            ProviderAuthorizationOutcomeStatus.FAILED,
            ProviderAccountErrorCode.SPOTIFY_WEBAPI_INVALID_CREDENTIALS,
        )


class PlaybackDriver:
    async def start(self, request: Any) -> ProviderLocalPairingChallenge:
        return ProviderLocalPairingChallenge(
            MusicProviderName.SPOTIFY,
            "f" * 16,
            datetime.now(UTC) + timedelta(minutes=5),
            1,
            "192.168.1.10:24879",
        )

    async def wait(self, challenge: Any) -> Any:
        await asyncio.Event().wait()

    async def cancel(self, flow_id: str) -> None:
        return None


class QueueSnapshotFake:
    async def snapshot(self) -> Any:
        raise AssertionError("not used")


class CacheStatsFake:
    async def stats(self, *, telegram_bot_id: int | None = None) -> Any:
        del telegram_bot_id
        raise AssertionError("not used")


async def _create_user(database: Database, telegram_id: int, role: UserRole) -> int:
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(telegram_id, first_name="Stage 13.4", role=role)
        return user.id


async def _setup(
    database: Database, driver: CaptureDriver
) -> tuple[Dispatcher, ProviderAccountManagementService, ProviderAuthorizationCoordinator, int]:
    owner_telegram_id = 134001
    owner_user_id = await _create_user(database, owner_telegram_id, UserRole.OWNER)
    authorization = TelegramAuthorizationService(database, owner_id=owner_telegram_id)
    coordinator = ProviderAuthorizationCoordinator(
        {
            (
                MusicProviderName.SPOTIFY,
                ProviderAuthorizationMethod.COMPOUND_CREDENTIALS,
            ): driver,
            (
                MusicProviderName.SPOTIFY,
                ProviderAuthorizationMethod.BROWSER_DEVICE_LINK,
            ): PlaybackDriver(),
        }
    )
    service = ProviderAccountManagementService(AccountBackend(), authorization, coordinator)
    i18n = LocalizationService(("en", "ru"), "en")
    users = TelegramUserService(database, i18n, owner_id=owner_telegram_id)
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_admin_router(
            AdminHandlerDependencies(
                users,
                AdminOverviewService(
                    database,
                    authorization,
                    QueueSnapshotFake(),
                    CacheStatsFake(),
                    telegram_bot_id=900,
                ),
                AdminPresentation(i18n),
                AdministratorManagementService(database, authorization, owner_id=owner_telegram_id),
                AdminManagementPresentation(i18n),
                provider_accounts=service,
                provider_accounts_presentation=ProviderAccountsPresentation(i18n),
            )
        )
    )
    return dispatcher, service, coordinator, owner_user_id


def _message_update(
    update_id: int,
    telegram_id: int,
    *,
    message_id: int,
    text: str,
    chat_type: ChatType = ChatType.PRIVATE,
) -> Update:
    user = TgUser(id=telegram_id, is_bot=False, first_name="User", language_code="en")
    return Update(
        update_id=update_id,
        message=Message(
            message_id=message_id,
            date=datetime.now(UTC),
            chat=Chat(id=telegram_id, type=chat_type),
            from_user=user,
            text=text,
        ),
    )


def _bot_side_effect(bot: Bot, events: list[str], *, fail_delete: bool = False) -> Any:
    async def call(method: Any, **_: Any) -> Any:
        if isinstance(method, DeleteMessage):
            events.append("delete")
            if fail_delete:
                raise RuntimeError(CLIENT_SECRET)
            return True
        if isinstance(method, SendMessage):
            events.append("send")
            return Message(
                message_id=999,
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type=ChatType.PRIVATE),
                text=method.text,
            ).as_(bot)
        if isinstance(method, EditMessageText):
            events.append("edit")
            return True
        return True

    return call


async def test_owner_submission_is_deleted_before_compound_child_handoff_and_never_leaks(
    database: Database, caplog: Any
) -> None:
    events: list[str] = []
    driver = CaptureDriver(events)
    dispatcher, service, coordinator, owner_user_id = await _setup(database, driver)
    started = await service.start_authorization(
        owner_user_id,
        MusicProviderName.SPOTIFY,
        ProviderAuthorizationMethod.COMPOUND_CREDENTIALS,
    )
    assert started.status is ProviderAuthorizationStartStatus.STARTED
    assert started.challenge is not None
    bot = Bot("123456:TEST_TOKEN")
    caplog.set_level(logging.DEBUG)
    mocked = AsyncMock(side_effect=_bot_side_effect(bot, events))
    try:
        with patch.object(Bot, "__call__", mocked):
            await dispatcher.feed_update(
                bot,
                _message_update(
                    1,
                    134001,
                    message_id=100,
                    text=f"{CLIENT_ID}\n{CLIENT_SECRET}",
                ),
            )
    finally:
        await bot.session.close()

    assert driver.calls == 1
    assert events.index("delete") < events.index("backend")
    outcome = await coordinator.wait(MusicProviderName.SPOTIFY, started.challenge.flow_id)
    assert outcome.error_code is ProviderAccountErrorCode.SPOTIFY_WEBAPI_INVALID_CREDENTIALS
    methods = [call.args[0] for call in mocked.await_args_list]
    assert isinstance(methods[0], DeleteMessage)
    assert all(CLIENT_ID not in repr(method) for method in methods)
    assert all(CLIENT_SECRET not in repr(method) for method in methods)
    assert CLIENT_SECRET not in caplog.text

    async with database.engine.connect() as connection:
        tables = (
            await connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ).scalars()
        rendered = []
        for table in tables:
            if str(table).startswith("sqlite_"):
                continue
            rows = (await connection.exec_driver_sql(f'SELECT * FROM "{table}"')).all()
            rendered.append(repr(rows))
    assert CLIENT_ID not in "".join(rendered)
    assert CLIENT_SECRET not in "".join(rendered)


async def test_delete_failure_fails_closed_with_zero_compound_child_calls(
    database: Database, caplog: Any
) -> None:
    events: list[str] = []
    driver = CaptureDriver(events)
    dispatcher, service, coordinator, owner_user_id = await _setup(database, driver)
    started = await service.start_authorization(
        owner_user_id,
        MusicProviderName.SPOTIFY,
        ProviderAuthorizationMethod.COMPOUND_CREDENTIALS,
    )
    assert started.challenge is not None
    bot = Bot("123456:TEST_TOKEN")
    caplog.set_level(logging.DEBUG)
    mocked = AsyncMock(side_effect=_bot_side_effect(bot, events, fail_delete=True))
    try:
        with patch.object(Bot, "__call__", mocked):
            await dispatcher.feed_update(
                bot,
                _message_update(
                    2,
                    134001,
                    message_id=101,
                    text=f"{CLIENT_ID}\n{CLIENT_SECRET}",
                ),
            )
    finally:
        await bot.session.close()

    outcome = await coordinator.wait(MusicProviderName.SPOTIFY, started.challenge.flow_id)
    assert outcome.error_code is ProviderAccountErrorCode.SPOTIFY_WEBAPI_MESSAGE_DELETE_FAILED
    assert driver.calls == 0
    assert events == ["delete", "send"]
    assert CLIENT_SECRET not in caplog.text


async def test_malformed_input_is_deleted_then_rejected_before_child_submission(
    database: Database,
) -> None:
    events: list[str] = []
    driver = CaptureDriver(events)
    dispatcher, service, coordinator, owner_user_id = await _setup(database, driver)
    started = await service.start_authorization(
        owner_user_id,
        MusicProviderName.SPOTIFY,
        ProviderAuthorizationMethod.COMPOUND_CREDENTIALS,
    )
    assert started.challenge is not None
    bot = Bot("123456:TEST_TOKEN")
    mocked = AsyncMock(side_effect=_bot_side_effect(bot, events))
    try:
        with patch.object(Bot, "__call__", mocked):
            await dispatcher.feed_update(
                bot,
                _message_update(3, 134001, message_id=102, text="only-one-line"),
            )
    finally:
        await bot.session.close()

    outcome = await coordinator.wait(MusicProviderName.SPOTIFY, started.challenge.flow_id)
    assert outcome.error_code is ProviderAccountErrorCode.SPOTIFY_WEBAPI_INVALID_FORMAT
    assert driver.calls == 0
    assert events[0] == "delete"


async def test_non_owner_group_and_no_flow_messages_are_not_consumed(
    database: Database,
) -> None:
    events: list[str] = []
    driver = CaptureDriver(events)
    dispatcher, service, coordinator, owner_user_id = await _setup(database, driver)
    await _create_user(database, 134002, UserRole.ADMIN)
    await _create_user(database, 134003, UserRole.USER)
    started = await service.start_authorization(
        owner_user_id,
        MusicProviderName.SPOTIFY,
        ProviderAuthorizationMethod.COMPOUND_CREDENTIALS,
    )
    assert started.challenge is not None
    bot = Bot("123456:TEST_TOKEN")
    mocked = AsyncMock(side_effect=_bot_side_effect(bot, events))
    try:
        with patch.object(Bot, "__call__", mocked):
            for update_id, telegram_id in ((4, 134002), (5, 134003)):
                await dispatcher.feed_update(
                    bot,
                    _message_update(
                        update_id,
                        telegram_id,
                        message_id=100 + update_id,
                        text=f"{CLIENT_ID}\n{CLIENT_SECRET}",
                    ),
                )
            await dispatcher.feed_update(
                bot,
                _message_update(
                    6,
                    134001,
                    message_id=106,
                    text=f"{CLIENT_ID}\n{CLIENT_SECRET}",
                    chat_type=ChatType.GROUP,
                ),
            )
    finally:
        await bot.session.close()
    assert driver.calls == 0
    assert events == []
    assert await coordinator.is_active(MusicProviderName.SPOTIFY)
    await coordinator.cancel(MusicProviderName.SPOTIFY, started.challenge.flow_id)

    bot = Bot("123456:TEST_TOKEN")
    mocked = AsyncMock(side_effect=_bot_side_effect(bot, events))
    try:
        with patch.object(Bot, "__call__", mocked):
            await dispatcher.feed_update(
                bot,
                _message_update(
                    7,
                    134001,
                    message_id=107,
                    text=f"{CLIENT_ID}\n{CLIENT_SECRET}",
                ),
            )
    finally:
        await bot.session.close()
    assert driver.calls == 0
    assert events == []


async def test_only_authoritative_owner_can_start_either_spotify_component(
    database: Database,
) -> None:
    events: list[str] = []
    driver = CaptureDriver(events)
    _, service, coordinator, owner_user_id = await _setup(database, driver)
    admin_id = await _create_user(database, 134012, UserRole.ADMIN)
    user_id = await _create_user(database, 134013, UserRole.USER)
    mismatched_owner_id = await _create_user(database, 134014, UserRole.OWNER)

    for method in (
        ProviderAuthorizationMethod.BROWSER_DEVICE_LINK,
        ProviderAuthorizationMethod.COMPOUND_CREDENTIALS,
    ):
        started = await service.start_authorization(
            owner_user_id, MusicProviderName.SPOTIFY, method
        )
        assert started.status is ProviderAuthorizationStartStatus.STARTED
        assert started.challenge is not None
        await coordinator.cancel(MusicProviderName.SPOTIFY, started.challenge.flow_id)

        for actor_id in (admin_id, user_id, mismatched_owner_id):
            with pytest.raises(AuthorizationError):
                await service.start_authorization(actor_id, MusicProviderName.SPOTIFY, method)
    assert driver.calls == 0
