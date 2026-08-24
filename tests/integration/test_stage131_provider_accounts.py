from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot, Dispatcher
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser

from app.core.enums import (
    MusicProviderName,
    ProviderHealthErrorCode,
    ProviderHealthStatus,
    UserRole,
)
from app.core.models import (
    ProviderHealthEntry,
    QueueRuntimeSnapshot,
    QueueStatusCounts,
    TelegramCacheStats,
    WorkerPoolSnapshot,
)
from app.core.provider_accounts import (
    ProviderAccountErrorCode,
    ProviderAccountOverview,
    ProviderAccountState,
    ProviderAccountStatus,
    ProviderAuthorizationMethod,
    ProviderAuthorizationOutcome,
    ProviderAuthorizationOutcomeStatus,
    ProviderAuthorizationRequest,
    ProviderCompoundCredentialInput,
    ProviderDisconnectOutcome,
    ProviderDisconnectOutcomeStatus,
    ProviderSecretInput,
    SensitiveValue,
)
from app.i18n import LocalizationService
from app.providers.account_management import (
    MANAGED_PROVIDER_ORDER,
    ProviderRuntimeAccountBackend,
)
from app.services.admin_management import AdministratorManagementService
from app.services.admin_overview import AdminOverviewService
from app.services.authorization import (
    AdminPermission,
    AuthorizationError,
    TelegramAuthorizationService,
)
from app.services.provider_accounts import ProviderAccountManagementService
from app.services.provider_authorization import ProviderAuthorizationCoordinator
from app.services.telegram_users import TelegramUserService
from app.storage import Database
from app.telegram.admin_handlers import AdminHandlerDependencies, create_admin_router
from app.telegram.admin_management_presentation import AdminManagementPresentation
from app.telegram.admin_presentation import AdminPresentation
from app.telegram.provider_accounts_presentation import (
    ProviderAccountsCallbackAction,
    ProviderAccountsPresentation,
    encode_provider_accounts_callback,
    parse_provider_accounts_callback,
)


class HealthProbe:
    def __init__(self) -> None:
        self.entries: dict[MusicProviderName, ProviderHealthEntry] = {
            provider: ProviderHealthEntry(provider, ProviderHealthStatus.READY, True, True)
            for provider in MANAGED_PROVIDER_ORDER
        }
        self.error: Exception | None = None
        self.refresh_error: Exception | None = None
        self.calls: list[MusicProviderName] = []
        self.refresh_calls = 0

    async def refresh_provider_health_state(self) -> None:
        self.refresh_calls += 1
        if self.refresh_error is not None:
            raise self.refresh_error

    async def check_provider_health(self, provider: MusicProviderName) -> ProviderHealthEntry:
        self.calls.append(provider)
        if self.error is not None:
            raise self.error
        return self.entries[provider]


class AccountBackend:
    def __init__(self) -> None:
        self.status_calls: list[MusicProviderName] = []
        self.reload_calls = 0
        self.disconnect_calls: list[MusicProviderName] = []
        self.failure: Exception | None = None

    async def get_account_status(self, provider: MusicProviderName) -> ProviderAccountStatus:
        self.status_calls.append(provider)
        if self.failure is not None:
            raise self.failure
        return ProviderAccountStatus(provider, ProviderAccountState.READY, datetime.now(UTC))

    async def reload_account_state(self) -> None:
        self.reload_calls += 1
        if self.failure is not None:
            raise self.failure

    async def disconnect_account(self, provider: MusicProviderName) -> ProviderDisconnectOutcome:
        self.disconnect_calls.append(provider)
        return ProviderDisconnectOutcome(provider, ProviderDisconnectOutcomeStatus.UNSUPPORTED)


class QueueSnapshotFake:
    async def snapshot(self) -> QueueRuntimeSnapshot:
        return QueueRuntimeSnapshot(
            download=WorkerPoolSnapshot(1, 1, 1, 2),
            upload=WorkerPoolSnapshot(1, 1, 1, 2),
            download_jobs=QueueStatusCounts(),
            upload_jobs=QueueStatusCounts(),
        )


class CacheStatsFake:
    async def stats(self, *, telegram_bot_id: int | None = None) -> TelegramCacheStats:
        del telegram_bot_id
        return TelegramCacheStats(0, 0, 0)


class BlockingDriver:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def authorize(
        self, request: ProviderAuthorizationRequest
    ) -> ProviderAuthorizationOutcome:
        self.started.set()
        await self.release.wait()
        return ProviderAuthorizationOutcome(
            request.provider, ProviderAuthorizationOutcomeStatus.READY
        )


class FailingDriver:
    async def authorize(
        self, request: ProviderAuthorizationRequest
    ) -> ProviderAuthorizationOutcome:
        del request
        raise RuntimeError("access_token=coordinator-secret")


async def _create_user(database: Database, telegram_id: int, role: UserRole = UserRole.USER) -> int:
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(
            telegram_id, first_name=f"user-{telegram_id}", role=role
        )
        return user.id


async def test_only_authoritative_owner_receives_provider_account_permission(
    database: Database,
) -> None:
    owner = await _create_user(database, 13101, UserRole.OWNER)
    admin = await _create_user(database, 13102, UserRole.ADMIN)
    user = await _create_user(database, 13103)
    stale_owner = await _create_user(database, 13104, UserRole.OWNER)
    authorization = TelegramAuthorizationService(database, owner_id=13101)

    owner_access = await authorization.get_access_context(owner)
    admin_access = await authorization.get_access_context(admin)
    assert AdminPermission.PROVIDER_ACCOUNTS_MANAGE in owner_access.permissions
    assert AdminPermission.PROVIDER_ACCOUNTS_MANAGE not in admin_access.permissions
    assert AdminPermission.PROVIDER_HEALTH_VIEW in admin_access.permissions
    for denied in (admin, user, stale_owner):
        with pytest.raises(AuthorizationError):
            await authorization.require_permission(denied, AdminPermission.PROVIDER_ACCOUNTS_MANAGE)


async def test_denied_service_calls_never_reach_backend(database: Database) -> None:
    owner = await _create_user(database, 13201, UserRole.OWNER)
    admin = await _create_user(database, 13202, UserRole.ADMIN)
    user = await _create_user(database, 13203)
    stale_owner = await _create_user(database, 13204, UserRole.OWNER)
    backend = AccountBackend()
    service = ProviderAccountManagementService(
        backend,
        TelegramAuthorizationService(database, owner_id=13201),
        ProviderAuthorizationCoordinator(),
    )

    for denied in (admin, user, stale_owner):
        with pytest.raises(AuthorizationError):
            await service.get_overview(denied)
        with pytest.raises(AuthorizationError):
            await service.disconnect(denied, MusicProviderName.TIDAL)
    assert not backend.status_calls
    assert not backend.disconnect_calls
    assert backend.reload_calls == 0
    assert len((await service.get_overview(owner)).accounts) == 3


async def test_runtime_status_normalization_and_no_authorization_advertisement() -> None:
    probe = HealthProbe()
    probe.entries[MusicProviderName.TIDAL] = ProviderHealthEntry(
        MusicProviderName.TIDAL, ProviderHealthStatus.READY, True, True
    )
    probe.entries[MusicProviderName.DEEZER] = ProviderHealthEntry(
        MusicProviderName.DEEZER,
        ProviderHealthStatus.AUTH_REQUIRED,
        True,
        True,
        ProviderHealthErrorCode.AUTH_NOT_CONFIGURED,
    )
    probe.entries[MusicProviderName.SPOTIFY] = ProviderHealthEntry(
        MusicProviderName.SPOTIFY,
        ProviderHealthStatus.AUTH_REQUIRED,
        True,
        True,
        ProviderHealthErrorCode.SESSION_UNAVAILABLE,
    )
    backend = ProviderRuntimeAccountBackend(probe)

    assert (await backend.get_account_status(MusicProviderName.TIDAL)).state is (
        ProviderAccountState.READY
    )
    deezer = await backend.get_account_status(MusicProviderName.DEEZER)
    assert deezer.state is ProviderAccountState.NOT_CONFIGURED
    assert deezer.error_code is ProviderAccountErrorCode.AUTH_NOT_CONFIGURED
    spotify = await backend.get_account_status(MusicProviderName.SPOTIFY)
    assert spotify.state is ProviderAccountState.DEGRADED
    assert spotify.error_code is ProviderAccountErrorCode.SESSION_UNAVAILABLE
    advertised = [
        (await backend.get_account_status(provider)).authorization_supported
        for provider in MANAGED_PROVIDER_ORDER
    ]
    assert not any(advertised)
    unsupported = await backend.get_account_status(MusicProviderName.QOBUZ)
    assert unsupported.state is ProviderAccountState.UNSUPPORTED


async def test_owner_reset_returns_only_the_sanitized_backend_outcome(
    database: Database,
) -> None:
    owner = await _create_user(database, 13251, UserRole.OWNER)

    class ResetBackend(AccountBackend):
        async def disconnect_account(
            self, provider: MusicProviderName
        ) -> ProviderDisconnectOutcome:
            self.disconnect_calls.append(provider)
            return ProviderDisconnectOutcome(provider, ProviderDisconnectOutcomeStatus.DISCONNECTED)

    backend = ResetBackend()
    service = ProviderAccountManagementService(
        backend,
        TelegramAuthorizationService(database, owner_id=13251),
        ProviderAuthorizationCoordinator(),
    )

    outcome = await service.disconnect(owner, MusicProviderName.DEEZER)

    assert outcome.status is ProviderDisconnectOutcomeStatus.DISCONNECTED
    assert backend.disconnect_calls == [MusicProviderName.DEEZER]
    assert "secret" not in repr(outcome).lower()


async def test_backend_and_refresh_failures_are_sanitized(database: Database) -> None:
    owner = await _create_user(database, 13301, UserRole.OWNER)
    probe = HealthProbe()
    probe.error = RuntimeError("access_token=top-secret cookie=top-secret arl=top-secret")
    backend = ProviderRuntimeAccountBackend(probe)
    status = await backend.get_account_status(MusicProviderName.TIDAL)
    assert status.state is ProviderAccountState.RECOVERING
    assert status.error_code is ProviderAccountErrorCode.RUNTIME_UNAVAILABLE
    assert "top-secret" not in repr(status)

    probe.error = None
    probe.refresh_error = RuntimeError("refresh_token=top-secret password=top-secret")
    service = ProviderAccountManagementService(
        backend,
        TelegramAuthorizationService(database, owner_id=13301),
        ProviderAuthorizationCoordinator(),
    )
    overview = await service.refresh(owner)
    assert all(account.state is ProviderAccountState.ERROR for account in overview.accounts)
    assert all(
        account.error_code is ProviderAccountErrorCode.REFRESH_FAILED
        for account in overview.accounts
    )
    assert "top-secret" not in repr(overview)


async def test_coordinator_unsupported_conflict_ready_cancel_and_cleanup() -> None:
    unsupported = ProviderAuthorizationCoordinator()
    request = ProviderAuthorizationRequest(
        MusicProviderName.TIDAL, ProviderAuthorizationMethod.BROWSER_DEVICE_LINK
    )
    result = await unsupported.authorize(request)
    assert result.status is ProviderAuthorizationOutcomeStatus.UNSUPPORTED

    driver = BlockingDriver()
    coordinator = ProviderAuthorizationCoordinator(
        {(MusicProviderName.TIDAL, request.method): driver}
    )
    first = asyncio.create_task(coordinator.authorize(request))
    await driver.started.wait()
    assert await coordinator.is_active(MusicProviderName.TIDAL)
    conflict = await coordinator.authorize(request)
    assert conflict.status is ProviderAuthorizationOutcomeStatus.ALREADY_ACTIVE
    driver.release.set()
    assert (await first).status is ProviderAuthorizationOutcomeStatus.READY
    await asyncio.sleep(0)
    assert not await coordinator.is_active(MusicProviderName.TIDAL)

    driver = BlockingDriver()
    coordinator = ProviderAuthorizationCoordinator(
        {(MusicProviderName.TIDAL, request.method): driver}
    )
    pending = asyncio.create_task(coordinator.authorize(request))
    await driver.started.wait()
    cancelled = await coordinator.cancel(MusicProviderName.TIDAL)
    assert cancelled.status is ProviderAuthorizationOutcomeStatus.CANCELLED
    with pytest.raises(asyncio.CancelledError):
        await pending
    await coordinator.close()

    failing = ProviderAuthorizationCoordinator(
        {(MusicProviderName.TIDAL, request.method): FailingDriver()}
    )
    failure = await failing.authorize(request)
    assert failure.status is ProviderAuthorizationOutcomeStatus.FAILED
    assert failure.error_code is ProviderAccountErrorCode.AUTHORIZATION_FAILED
    assert "coordinator-secret" not in repr(failure)


async def test_service_projects_an_active_flow_as_authorizing(database: Database) -> None:
    owner = await _create_user(database, 13351, UserRole.OWNER)
    driver = BlockingDriver()
    request = ProviderAuthorizationRequest(
        MusicProviderName.TIDAL, ProviderAuthorizationMethod.BROWSER_DEVICE_LINK
    )
    coordinator = ProviderAuthorizationCoordinator(
        {(MusicProviderName.TIDAL, request.method): driver}
    )
    service = ProviderAccountManagementService(
        AccountBackend(),
        TelegramAuthorizationService(database, owner_id=13351),
        coordinator,
    )
    pending = asyncio.create_task(coordinator.authorize(request))
    await driver.started.wait()

    status = await service.get_status(owner, MusicProviderName.TIDAL)
    assert status.state is ProviderAccountState.AUTHORIZING
    driver.release.set()
    assert (await pending).status is ProviderAuthorizationOutcomeStatus.READY


def test_sensitive_inputs_and_returned_dtos_have_sanitized_repr() -> None:
    secret = SensitiveValue("credential-marker-131")
    simple = ProviderSecretInput(MusicProviderName.DEEZER, secret)
    compound = ProviderCompoundCredentialInput(
        MusicProviderName.SPOTIFY,
        SensitiveValue("playback-marker-131"),
        SensitiveValue("api-marker-131"),
    )
    status = ProviderAccountStatus(
        MusicProviderName.DEEZER,
        ProviderAccountState.NOT_CONFIGURED,
        datetime.now(UTC),
        error_code=ProviderAccountErrorCode.AUTH_NOT_CONFIGURED,
    )
    overview = ProviderAccountOverview(datetime.now(UTC), (status,))

    rendered = repr((simple, compound, status, overview))
    for marker in ("credential-marker-131", "playback-marker-131", "api-marker-131"):
        assert marker not in rendered
    assert secret.reveal_to_provider_backend() == "credential-marker-131"


def test_owner_only_menu_visibility_callbacks_and_presentation_are_secret_free() -> None:
    i18n = LocalizationService(("en", "ru"), "en")
    admin_presentation = AdminPresentation(i18n)
    owner_callbacks = [
        button.callback_data
        for row in admin_presentation.keyboard("en", authoritative_owner=True).inline_keyboard
        for button in row
    ]
    admin_callbacks = [
        button.callback_data
        for row in admin_presentation.keyboard("en", authoritative_owner=False).inline_keyboard
        for button in row
    ]
    assert "adm5:o" in owner_callbacks
    assert all(not (value or "").startswith("adm5:") for value in admin_callbacks)

    statuses = tuple(
        ProviderAccountStatus(provider, ProviderAccountState.READY, datetime.now(UTC))
        for provider in MANAGED_PROVIDER_ORDER
    )
    overview = ProviderAccountOverview(datetime.now(UTC), statuses)
    presentation = ProviderAccountsPresentation(i18n)
    text = presentation.overview_text(overview, "en")
    keyboard = presentation.overview_keyboard(overview, "en")
    callback_values = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    serialized = repr((text, callback_values)).lower()
    for forbidden in ("access_token", "refresh_token", "cookie=", "arl=", "password="):
        assert forbidden not in serialized
    assert all(value is not None and len(value.encode()) <= 64 for value in callback_values)
    assert all(
        "connect" not in button.text.lower() for row in keyboard.inline_keyboard for button in row
    )


def test_adm5_parser_is_strict_and_provider_neutral() -> None:
    parsed = parse_provider_accounts_callback("adm5:d:tidal")
    assert parsed is not None
    assert parsed.action is ProviderAccountsCallbackAction.DETAIL
    assert parsed.provider is MusicProviderName.TIDAL
    assert parse_provider_accounts_callback("adm5:d") is None
    assert parse_provider_accounts_callback("adm5:o:tidal") is None
    assert parse_provider_accounts_callback("adm5:r:unknown") is None
    assert parse_provider_accounts_callback("adm4:r") is None
    assert (
        encode_provider_accounts_callback(
            ProviderAccountsCallbackAction.REFRESH, MusicProviderName.SPOTIFY
        )
        == "adm5:r:spotify"
    )


async def test_forged_callbacks_private_chat_and_owner_execution(database: Database) -> None:
    await _create_user(database, 13401, UserRole.OWNER)
    await _create_user(database, 13402, UserRole.ADMIN)
    await _create_user(database, 13403)
    await _create_user(database, 13404, UserRole.OWNER)
    i18n = LocalizationService(("en", "ru"), "en")
    authorization = TelegramAuthorizationService(database, owner_id=13401)
    backend = AccountBackend()
    account_service = ProviderAccountManagementService(
        backend, authorization, ProviderAuthorizationCoordinator()
    )
    overview = AdminOverviewService(
        database,
        authorization,
        QueueSnapshotFake(),
        CacheStatsFake(),
        telegram_bot_id=900,
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_admin_router(
            AdminHandlerDependencies(
                TelegramUserService(database, i18n, owner_id=13401),
                overview,
                AdminPresentation(i18n),
                AdministratorManagementService(database, authorization, owner_id=13401),
                AdminManagementPresentation(i18n),
                provider_accounts=account_service,
                provider_accounts_presentation=ProviderAccountsPresentation(i18n),
            )
        )
    )

    def callback(
        update_id: int,
        telegram_id: int,
        data: str,
        chat_type: ChatType = ChatType.PRIVATE,
    ) -> Update:
        user = TgUser(id=telegram_id, is_bot=False, first_name="User", language_code="en")
        return Update(
            update_id=update_id,
            callback_query=CallbackQuery(
                id=f"accounts-{update_id}",
                from_user=user,
                chat_instance="chat",
                message=Message(
                    message_id=50,
                    date=datetime.now(UTC),
                    chat=Chat(id=telegram_id, type=chat_type),
                    from_user=user,
                    text="panel",
                ),
                data=data,
            ),
        )

    bot = Bot("123456:TEST_TOKEN")
    try:
        with patch.object(Bot, "__call__", new_callable=AsyncMock, return_value=True):
            with patch(
                "app.telegram.admin_handlers.parse_provider_accounts_callback",
                wraps=parse_provider_accounts_callback,
            ) as parser:
                for number, telegram_id in enumerate((13402, 13403, 13404), start=1):
                    await dispatcher.feed_update(bot, callback(number, telegram_id, "adm5:y:tidal"))
                assert parser.call_count == 0
            assert not backend.status_calls
            assert not backend.disconnect_calls

            await dispatcher.feed_update(bot, callback(4, 13401, "adm5:not-valid"))
            assert not backend.status_calls

            with patch(
                "app.telegram.admin_handlers.parse_provider_accounts_callback",
                wraps=parse_provider_accounts_callback,
            ) as parser:
                await dispatcher.feed_update(bot, callback(5, 13401, "adm5:o", ChatType.GROUP))
                assert parser.call_count == 0
            assert not backend.status_calls

            await dispatcher.feed_update(bot, callback(6, 13401, "adm5:o"))
            assert tuple(backend.status_calls) == MANAGED_PROVIDER_ORDER
    finally:
        await bot.session.close()


async def test_no_provider_account_credentials_or_pending_sessions_are_persisted(
    database: Database,
) -> None:
    async with database.engine.connect() as connection:
        tables = tuple(
            str(value)
            for value in (
                await connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ).scalars()
        )
    assert not any("provider_account" in table for table in tables)
    assert not any("authorization_session" in table for table in tables)
