from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.enums import ChatType
from aiogram.methods import DeleteMessage, EditMessageText, SendMessage
from aiogram.types import Chat, Message, MessageOriginUser, Update
from aiogram.types import User as TgUser

from app.core.enums import MusicProviderName, UserRole
from app.core.provider_accounts import (
    ProviderAccountErrorCode,
    ProviderAccountState,
    ProviderAccountStatus,
    ProviderAuthorizationMethod,
    ProviderAuthorizationOutcome,
    ProviderAuthorizationOutcomeStatus,
    ProviderAuthorizationStartStatus,
    ProviderDisconnectOutcome,
    ProviderDisconnectOutcomeStatus,
    ProviderSecretInput,
)
from app.i18n import LocalizationService
from app.services.admin_management import AdministratorManagementService
from app.services.admin_overview import AdminOverviewService
from app.services.authorization import TelegramAuthorizationService
from app.services.provider_accounts import ProviderAccountManagementService
from app.services.provider_authorization import ProviderAuthorizationCoordinator
from app.services.telegram_users import TelegramUserService
from app.storage import Database
from app.telegram.admin_handlers import AdminHandlerDependencies, create_admin_router
from app.telegram.admin_management_presentation import AdminManagementPresentation
from app.telegram.admin_presentation import AdminPresentation
from app.telegram.provider_accounts_presentation import ProviderAccountsPresentation

SECRET = "stage133-telegram-distinctive-ARL_987654321"


class AccountBackend:
    async def get_account_status(self, provider: MusicProviderName) -> ProviderAccountStatus:
        return ProviderAccountStatus(
            provider, ProviderAccountState.NOT_CONFIGURED, datetime.now(UTC)
        )

    async def reload_account_state(self) -> None:
        return None

    async def disconnect_account(self, provider: MusicProviderName) -> ProviderDisconnectOutcome:
        return ProviderDisconnectOutcome(provider, ProviderDisconnectOutcomeStatus.UNSUPPORTED)


class CaptureDriver:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls = 0

    async def authorize_secret(
        self, credential: ProviderSecretInput
    ) -> ProviderAuthorizationOutcome:
        self.events.append("backend")
        self.calls += 1
        assert credential.secret.reveal_to_provider_backend() == SECRET
        return ProviderAuthorizationOutcome(
            MusicProviderName.DEEZER,
            ProviderAuthorizationOutcomeStatus.FAILED,
            ProviderAccountErrorCode.DEEZER_ARL_INVALID,
        )


class QueueSnapshotFake:
    async def get_snapshot(self) -> Any:
        raise AssertionError("not used by the sensitive-input route")


class CacheStatsFake:
    async def get_stats(self) -> Any:
        raise AssertionError("not used by the sensitive-input route")


async def _create_user(database: Database, telegram_id: int, role: UserRole) -> int:
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(telegram_id, first_name="Stage 13.3", role=role)
        return user.id


async def _setup(
    database: Database,
    driver: CaptureDriver,
) -> tuple[Dispatcher, ProviderAccountManagementService, ProviderAuthorizationCoordinator, int]:
    owner_telegram_id = 133301
    owner_user_id = await _create_user(database, owner_telegram_id, UserRole.OWNER)
    authorization = TelegramAuthorizationService(database, owner_id=owner_telegram_id)
    coordinator = ProviderAuthorizationCoordinator(
        {
            (
                MusicProviderName.DEEZER,
                ProviderAuthorizationMethod.SENSITIVE_SECRET,
            ): driver
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
    text: str | None = SECRET,
    caption: str | None = None,
    chat_type: ChatType = ChatType.PRIVATE,
    forwarded: bool = False,
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
            caption=caption,
            forward_origin=(
                MessageOriginUser(date=datetime.now(UTC), sender_user=user) if forwarded else None
            ),
        ),
    )


def _bot_side_effect(bot: Bot, events: list[str], *, fail_delete: bool = False) -> Any:
    async def call(method: Any, **_: Any) -> Any:
        if isinstance(method, DeleteMessage):
            events.append("delete")
            if fail_delete:
                raise RuntimeError(SECRET)
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


async def test_owner_private_message_is_deleted_before_submission_and_never_leaks(
    database: Database, caplog: Any
) -> None:
    events: list[str] = []
    driver = CaptureDriver(events)
    dispatcher, service, coordinator, owner_user_id = await _setup(database, driver)
    started = await service.start_authorization(owner_user_id, MusicProviderName.DEEZER)
    assert started.status is ProviderAuthorizationStartStatus.STARTED
    assert started.challenge is not None

    bot = Bot("123456:TEST_TOKEN")
    caplog.set_level(logging.DEBUG)
    mocked = AsyncMock(side_effect=_bot_side_effect(bot, events))
    try:
        with patch.object(Bot, "__call__", mocked):
            await dispatcher.feed_update(bot, _message_update(1, 133301, message_id=100))
    finally:
        await bot.session.close()

    assert driver.calls == 1
    assert events.index("delete") < events.index("backend")
    outcome = await coordinator.wait(MusicProviderName.DEEZER, started.challenge.flow_id)
    assert outcome.error_code is ProviderAccountErrorCode.DEEZER_ARL_INVALID
    methods = [call.args[0] for call in mocked.await_args_list]
    assert isinstance(methods[0], DeleteMessage)
    assert all(SECRET not in repr(method) for method in methods)
    assert SECRET not in caplog.text
    assert SECRET not in repr(outcome)
    assert not await coordinator.is_active(MusicProviderName.DEEZER)

    async with database.engine.connect() as connection:
        tables = (
            await connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ).scalars()
        database_rendered = []
        for table in tables:
            if str(table).startswith("sqlite_"):
                continue
            rows = (await connection.exec_driver_sql(f'SELECT * FROM "{table}"')).all()
            database_rendered.append(repr(rows))
    assert SECRET not in "".join(database_rendered)


async def test_delete_failure_fails_closed_and_makes_zero_provider_calls(
    database: Database, caplog: Any
) -> None:
    events: list[str] = []
    driver = CaptureDriver(events)
    dispatcher, service, coordinator, owner_user_id = await _setup(database, driver)
    started = await service.start_authorization(owner_user_id, MusicProviderName.DEEZER)
    assert started.challenge is not None
    bot = Bot("123456:TEST_TOKEN")
    caplog.set_level(logging.DEBUG)
    mocked = AsyncMock(side_effect=_bot_side_effect(bot, events, fail_delete=True))
    try:
        with patch.object(Bot, "__call__", mocked):
            await dispatcher.feed_update(bot, _message_update(2, 133301, message_id=101))
    finally:
        await bot.session.close()

    outcome = await coordinator.wait(MusicProviderName.DEEZER, started.challenge.flow_id)
    assert outcome.error_code is ProviderAccountErrorCode.DEEZER_AUTH_MESSAGE_DELETE_FAILED
    assert driver.calls == 0
    assert events == ["delete", "send"]
    outgoing = [
        call.args[0].text
        for call in mocked.await_args_list
        if isinstance(call.args[0], SendMessage)
    ]
    assert outgoing == [
        "The sensitive message could not be removed, so Deezer authorization was not performed."
    ]
    assert SECRET not in repr(outgoing)
    assert SECRET not in caplog.text
    assert not await coordinator.is_active(MusicProviderName.DEEZER)


async def test_non_owner_group_forwarded_and_no_flow_messages_are_not_consumed(
    database: Database,
) -> None:
    events: list[str] = []
    driver = CaptureDriver(events)
    dispatcher, service, coordinator, owner_user_id = await _setup(database, driver)
    await _create_user(database, 133302, UserRole.ADMIN)
    await _create_user(database, 133303, UserRole.USER)
    await _create_user(database, 133304, UserRole.OWNER)
    started = await service.start_authorization(owner_user_id, MusicProviderName.DEEZER)
    assert started.challenge is not None
    bot = Bot("123456:TEST_TOKEN")
    mocked = AsyncMock(side_effect=_bot_side_effect(bot, events))
    try:
        with patch.object(Bot, "__call__", mocked):
            await dispatcher.feed_update(bot, _message_update(3, 133302, message_id=102))
            await dispatcher.feed_update(bot, _message_update(4, 133303, message_id=103))
            await dispatcher.feed_update(bot, _message_update(5, 133304, message_id=104))
            await dispatcher.feed_update(
                bot,
                _message_update(6, 133301, message_id=105, chat_type=ChatType.GROUP),
            )
            await dispatcher.feed_update(
                bot, _message_update(7, 133301, message_id=106, forwarded=True)
            )
            await dispatcher.feed_update(
                bot,
                _message_update(
                    8,
                    133301,
                    message_id=107,
                    text=None,
                    caption=SECRET,
                ),
            )
    finally:
        await bot.session.close()

    assert driver.calls == 0
    assert events == []
    assert await coordinator.is_active(MusicProviderName.DEEZER)
    await coordinator.cancel(MusicProviderName.DEEZER, started.challenge.flow_id)

    bot = Bot("123456:TEST_TOKEN")
    mocked = AsyncMock(side_effect=_bot_side_effect(bot, events))
    try:
        with patch.object(Bot, "__call__", mocked):
            await dispatcher.feed_update(bot, _message_update(9, 133301, message_id=108))
    finally:
        await bot.session.close()
    assert driver.calls == 0
    assert events == []


async def test_consumed_telegram_message_cannot_be_replayed_into_new_generation(
    database: Database,
) -> None:
    events: list[str] = []
    driver = CaptureDriver(events)
    dispatcher, service, coordinator, owner_user_id = await _setup(database, driver)
    first = await service.start_authorization(owner_user_id, MusicProviderName.DEEZER)
    assert first.challenge is not None
    bot = Bot("123456:TEST_TOKEN")
    mocked = AsyncMock(side_effect=_bot_side_effect(bot, events))
    try:
        with patch.object(Bot, "__call__", mocked):
            received = _message_update(9, 133301, message_id=200)
            await dispatcher.feed_update(bot, received)
            await asyncio.sleep(0)
            second = await service.start_authorization(owner_user_id, MusicProviderName.DEEZER)
            assert second.status is ProviderAuthorizationStartStatus.STARTED
            await dispatcher.feed_update(bot, _message_update(10, 133301, message_id=200))
    finally:
        await bot.session.close()

    assert driver.calls == 1
    assert events.count("delete") == 1
    assert second.challenge is not None
    assert await coordinator.is_active(MusicProviderName.DEEZER)
    await coordinator.cancel(MusicProviderName.DEEZER, second.challenge.flow_id)
