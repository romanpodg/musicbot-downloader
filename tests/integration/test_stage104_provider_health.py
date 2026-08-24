from __future__ import annotations

import asyncio
from collections.abc import Mapping
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
    ProviderResolutionStatus,
    ProviderRuntimeStatus,
    UserRole,
)
from app.core.models import (
    ProviderCapabilities,
    ProviderHealthEntry,
    ProviderMediaCapabilities,
    ProviderSourceCheck,
    QueueRuntimeSnapshot,
    QueueStatusCounts,
    TelegramCacheStats,
    WorkerPoolSnapshot,
)
from app.i18n import LocalizationService
from app.providers.base import MusicProvider, ProviderAvailability, TrackReference
from app.providers.onthespot.capabilities import ONTHESPOT_CAPABILITIES, PROVIDER_ORDER
from app.services.admin_management import AdministratorManagementService
from app.services.admin_overview import AdminOverviewService
from app.services.authorization import (
    AdminPermission,
    AuthorizationError,
    TelegramAuthorizationService,
)
from app.services.provider_health import ProviderHealthService
from app.services.provider_resolution import ProviderResolver
from app.services.telegram_users import TelegramUserService
from app.storage import Database
from app.telegram.admin_handlers import AdminHandlerDependencies, create_admin_router
from app.telegram.admin_management_presentation import AdminManagementPresentation
from app.telegram.admin_presentation import AdminPresentation
from app.telegram.provider_health_presentation import ProviderHealthPresentation


class FakeHealthProbe:
    def __init__(self) -> None:
        self.statuses: dict[MusicProviderName, ProviderHealthStatus] = {
            provider: ProviderHealthStatus.READY for provider in PROVIDER_ORDER
        }
        self.errors: dict[MusicProviderName, Exception] = {}
        self.delays: dict[MusicProviderName, float] = {}
        self.calls: list[MusicProviderName] = []
        self.refresh_calls = 0
        self.refresh_error: Exception | None = None

    async def refresh_provider_health_state(self) -> None:
        self.refresh_calls += 1
        if self.refresh_error is not None:
            raise self.refresh_error

    async def check_provider_health(self, provider: MusicProviderName) -> ProviderHealthEntry:
        self.calls.append(provider)
        if delay := self.delays.get(provider):
            await asyncio.sleep(delay)
        if error := self.errors.get(provider):
            raise error
        capabilities = ONTHESPOT_CAPABILITIES[provider]
        return ProviderHealthEntry(
            provider,
            self.statuses[provider],
            bool(capabilities.requires_auth),
            capabilities.download_supported,
        )


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


class RuntimeSourceProvider(MusicProvider):
    def __init__(self, status: ProviderRuntimeStatus) -> None:
        self.status = status

    async def availability(self) -> ProviderAvailability:
        return ProviderAvailability(True)

    def detect_url(self, url: str) -> TrackReference:
        raise AssertionError(url)

    async def get_metadata(self, url: str) -> object:  # type: ignore[override]
        raise AssertionError(url)

    async def list_searchable_providers(self) -> tuple[MusicProviderName, ...]:
        raise AssertionError("Stage 4 must not search")

    async def search_tracks(self, request: object) -> list[object]:  # type: ignore[override]
        raise AssertionError(request)

    def provider_capabilities(self, provider: MusicProviderName) -> ProviderCapabilities:
        del provider
        return ProviderCapabilities(
            metadata_supported=True,
            search_supported=True,
            download_supported=True,
            requires_auth=True,
            media=ProviderMediaCapabilities(known=False),
        )

    async def check_source(
        self, provider: MusicProviderName, provider_track_id: str
    ) -> ProviderSourceCheck:
        del provider, provider_track_id
        return ProviderSourceCheck(self.status)


async def _create_user(database: Database, telegram_id: int, role: UserRole = UserRole.USER) -> int:
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(
            telegram_id, first_name=f"user-{telegram_id}", role=role
        )
        return user.id


async def _set_role(database: Database, user_id: int, role: UserRole) -> None:
    async with database.transaction() as repositories:
        user = await repositories.users.get(user_id)
        assert user is not None
        await repositories.users.set_role(user, role)


async def _table_counts(database: Database) -> Mapping[str, int]:
    async with database.engine.connect() as connection:
        names = (
            await connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ).scalars()
        counts: dict[str, int] = {}
        for name in names:
            if name == "sqlite_sequence":
                continue
            value = (
                await connection.exec_driver_sql(f'SELECT COUNT(*) FROM "{name}"')
            ).scalar_one()
            counts[str(name)] = int(value)
        return counts


async def test_authorization_is_fresh_and_denials_perform_zero_probes(
    database: Database,
) -> None:
    user_id = await _create_user(database, 4101)
    admin_id = await _create_user(database, 4102, UserRole.ADMIN)
    owner_id = await _create_user(database, 4103, UserRole.OWNER)
    stale_owner_id = await _create_user(database, 4104, UserRole.OWNER)
    probe = FakeHealthProbe()
    authorization = TelegramAuthorizationService(database, owner_id=4103)
    service = ProviderHealthService(probe, authorization)

    assert (
        AdminPermission.PROVIDER_HEALTH_VIEW
        in (await authorization.get_access_context(admin_id)).permissions
    )
    assert (
        AdminPermission.PROVIDER_HEALTH_VIEW
        in (await authorization.get_access_context(owner_id)).permissions
    )
    assert not (await authorization.get_access_context(user_id)).permissions
    assert not (await authorization.get_access_context(stale_owner_id)).permissions

    for denied_id in (user_id, stale_owner_id):
        before = len(probe.calls)
        with pytest.raises(AuthorizationError):
            await service.check_all(denied_id)
        assert len(probe.calls) == before

    assert len((await service.check_all(admin_id)).entries) == len(PROVIDER_ORDER)
    assert len((await service.check_all(owner_id)).entries) == len(PROVIDER_ORDER)

    await _set_role(database, admin_id, UserRole.USER)
    before = len(probe.calls)
    with pytest.raises(AuthorizationError):
        await service.check_all(admin_id)
    assert len(probe.calls) == before

    await _set_role(database, user_id, UserRole.ADMIN)
    assert len((await service.check_all(user_id)).entries) == len(PROVIDER_ORDER)


async def test_all_providers_failure_timeout_freshness_and_no_persistence(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin_id = await _create_user(database, 4201, UserRole.ADMIN)
    probe = FakeHealthProbe()
    service = ProviderHealthService(probe, TelegramAuthorizationService(database, owner_id=None))
    before = await _table_counts(database)

    probe.statuses[MusicProviderName.QOBUZ] = ProviderHealthStatus.AUTH_REQUIRED
    probe.errors[MusicProviderName.TIDAL] = RuntimeError(
        "access_token=secret refresh_token=secret cookie=secret password=secret"
    )
    probe.delays[MusicProviderName.SPOTIFY] = 0.05
    monkeypatch.setattr("app.services.provider_health.PROVIDER_HEALTH_TIMEOUT_SECONDS", 0.01)
    first = await service.check_all(admin_id)

    assert tuple(entry.provider for entry in first.entries) == PROVIDER_ORDER
    by_provider = {entry.provider: entry for entry in first.entries}
    assert by_provider[MusicProviderName.QOBUZ].status is ProviderHealthStatus.AUTH_REQUIRED
    assert by_provider[MusicProviderName.TIDAL].error_code is ProviderHealthErrorCode.UPSTREAM_ERROR
    assert (
        by_provider[MusicProviderName.SPOTIFY].error_code
        is ProviderHealthErrorCode.HEALTH_CHECK_TIMEOUT
    )
    assert "secret" not in repr(first)

    probe.errors.clear()
    probe.delays.clear()
    probe.statuses[MusicProviderName.QOBUZ] = ProviderHealthStatus.READY
    second = await service.check_all(admin_id)
    assert second is not first
    assert second.checked_at >= first.checked_at
    assert {entry.provider: entry for entry in second.entries}[
        MusicProviderName.QOBUZ
    ].status is ProviderHealthStatus.READY
    assert await _table_counts(database) == before


async def test_concurrent_observation_coalesces_and_caller_cancellation_is_isolated(
    database: Database,
) -> None:
    admin_id = await _create_user(database, 4301, UserRole.ADMIN)
    owner_id = await _create_user(database, 4302, UserRole.OWNER)
    probe = FakeHealthProbe()
    probe.delays[MusicProviderName.APPLE_MUSIC] = 0.05
    service = ProviderHealthService(probe, TelegramAuthorizationService(database, owner_id=4302))

    cancelled = asyncio.create_task(service.check_all(admin_id))
    survivor = asyncio.create_task(service.check_all(owner_id))
    await asyncio.sleep(0.01)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    snapshot = await survivor
    assert len(snapshot.entries) == len(PROVIDER_ORDER)
    assert probe.refresh_calls == 0
    assert probe.calls.count(MusicProviderName.APPLE_MUSIC) == 1
    assert len(probe.calls) == len(PROVIDER_ORDER)

    await service.check_all(admin_id)
    assert len(probe.calls) == len(PROVIDER_ORDER) * 2
    assert probe.refresh_calls == 0


async def test_health_observation_never_invokes_mutating_runtime_refresh(
    database: Database,
) -> None:
    admin_id = await _create_user(database, 4351, UserRole.ADMIN)
    probe = FakeHealthProbe()
    probe.refresh_error = RuntimeError(
        "authorization: Bearer secret; cookie=secret; password=secret"
    )
    service = ProviderHealthService(probe, TelegramAuthorizationService(database, owner_id=None))

    snapshot = await service.check_all(admin_id)

    assert tuple(entry.provider for entry in snapshot.entries) == PROVIDER_ORDER
    assert all(entry.status is ProviderHealthStatus.READY for entry in snapshot.entries)
    assert probe.refresh_calls == 0
    assert tuple(probe.calls) == PROVIDER_ORDER
    assert "secret" not in repr(snapshot)


async def test_router_probes_only_authorized_provider_health_actions(
    database: Database,
) -> None:
    user_id = await _create_user(database, 4401)
    admin_id = await _create_user(database, 4402, UserRole.ADMIN)
    await _create_user(database, 4403, UserRole.OWNER)
    stale_owner_id = await _create_user(database, 4404, UserRole.OWNER)
    del user_id, stale_owner_id
    i18n = LocalizationService(("en", "ru"), "en")
    authorization = TelegramAuthorizationService(database, owner_id=4403)
    users = TelegramUserService(database, i18n, owner_id=4403)
    overview = AdminOverviewService(
        database,
        authorization,
        QueueSnapshotFake(),
        CacheStatsFake(),
        telegram_bot_id=900,
    )
    probe = FakeHealthProbe()
    health = ProviderHealthService(probe, authorization)
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_admin_router(
            AdminHandlerDependencies(
                users,
                overview,
                AdminPresentation(i18n),
                AdministratorManagementService(database, authorization, owner_id=4403),
                AdminManagementPresentation(i18n),
                provider_health=health,
                provider_health_presentation=ProviderHealthPresentation(i18n),
            )
        )
    )

    def callback(update_id: int, telegram_id: int, data: str) -> Update:
        user = TgUser(id=telegram_id, is_bot=False, first_name="User", language_code="en")
        return Update(
            update_id=update_id,
            callback_query=CallbackQuery(
                id=f"provider-health-{update_id}",
                from_user=user,
                chat_instance="chat",
                message=Message(
                    message_id=50,
                    date=datetime.now(UTC),
                    chat=Chat(id=telegram_id, type=ChatType.PRIVATE),
                    from_user=user,
                    text="panel",
                ),
                data=data,
            ),
        )

    bot = Bot("123456:TEST_TOKEN")
    try:
        with patch.object(Bot, "__call__", new_callable=AsyncMock, return_value=True):
            for number, telegram_id in enumerate((4401, 4404), start=1):
                await dispatcher.feed_update(bot, callback(number, telegram_id, "adm4:h"))
                assert not probe.calls

            # Ordinary admin/worker/management navigation remains provider-network-free.
            await dispatcher.feed_update(bot, callback(3, 4402, "adm1:refresh"))
            await dispatcher.feed_update(bot, callback(4, 4402, "adm3:w"))
            await dispatcher.feed_update(bot, callback(5, 4403, "adm2:l:0"))
            assert not probe.calls

            await dispatcher.feed_update(bot, callback(6, 4402, "adm4:h"))
            assert len(probe.calls) == len(PROVIDER_ORDER)

            await _set_role(database, admin_id, UserRole.USER)
            before = len(probe.calls)
            await dispatcher.feed_update(bot, callback(7, 4402, "adm4:r"))
            assert len(probe.calls) == before

            await _set_role(database, admin_id, UserRole.ADMIN)
            await dispatcher.feed_update(bot, callback(8, 4402, "adm4:r"))
            assert len(probe.calls) == before + len(PROVIDER_ORDER)

            before = len(probe.calls)
            await dispatcher.feed_update(bot, callback(9, 4402, "adm4:unknown"))
            assert len(probe.calls) == before
    finally:
        await bot.session.close()


async def test_stale_health_neither_allows_nor_blocks_fresh_stage4_resolution(
    database: Database,
) -> None:
    admin_id = await _create_user(database, 4501, UserRole.ADMIN)
    health_probe = FakeHealthProbe()
    health = ProviderHealthService(
        health_probe, TelegramAuthorizationService(database, owner_id=None)
    )
    async with database.transaction() as repositories:
        track = await repositories.tracks.create_track(title="Independent", artist="Health")
        await repositories.track_sources.upsert_source(
            track_id=track.id,
            provider=MusicProviderName.QOBUZ,
            provider_track_id="qobuz-source",
            url=None,
        )

    health_probe.statuses[MusicProviderName.QOBUZ] = ProviderHealthStatus.AUTH_REQUIRED
    non_ready = await health.check_all(admin_id)
    assert {entry.provider: entry.status for entry in non_ready.entries}[
        MusicProviderName.QOBUZ
    ] is ProviderHealthStatus.AUTH_REQUIRED
    runtime = RuntimeSourceProvider(ProviderRuntimeStatus.AVAILABLE)
    resolved = await ProviderResolver(database, runtime).resolve(track.id)
    assert resolved.status is ProviderResolutionStatus.AVAILABLE
    assert resolved.candidates[0].provider is MusicProviderName.QOBUZ

    health_probe.statuses[MusicProviderName.QOBUZ] = ProviderHealthStatus.READY
    ready = await health.check_all(admin_id)
    assert {entry.provider: entry.status for entry in ready.entries}[
        MusicProviderName.QOBUZ
    ] is ProviderHealthStatus.READY
    runtime.status = ProviderRuntimeStatus.AUTH_REQUIRED
    rejected = await ProviderResolver(database, runtime).resolve(track.id)
    assert rejected.status is ProviderResolutionStatus.NO_AVAILABLE_PROVIDER
    assert rejected.failures[0].runtime_status is ProviderRuntimeStatus.AUTH_REQUIRED
