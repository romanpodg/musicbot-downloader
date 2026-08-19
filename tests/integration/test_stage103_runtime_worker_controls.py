from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot, Dispatcher
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser

from app.config import Settings
from app.core.enums import QualityProfile, QueueJobStatus, UserRole
from app.core.exceptions import WorkerLimitError
from app.core.models import (
    QueueRuntimeSnapshot,
    QueueStatusCounts,
    TelegramCacheStats,
    WorkerPoolSnapshot,
)
from app.i18n import LocalizationService
from app.services.admin_management import AdministratorManagementService
from app.services.admin_overview import AdminOverviewService
from app.services.artifacts import DownloadArtifactManager
from app.services.authorization import (
    AdminPermission,
    AuthorizationError,
    TelegramAuthorizationService,
)
from app.services.provider_resolution import ProviderResolver
from app.services.queues import (
    DownloadQueueService,
    UploadQueueService,
    WorkerSettingsService,
)
from app.services.runtime_worker_control import (
    RuntimeWorkerControlService,
    WorkerMutationStatus,
)
from app.services.telegram_users import TelegramUserService
from app.services.workers import QueueManager
from app.storage import Database
from app.telegram.admin_handlers import AdminHandlerDependencies, create_admin_router
from app.telegram.admin_management_presentation import AdminManagementPresentation
from app.telegram.admin_presentation import AdminPresentation
from app.telegram.worker_control_presentation import WorkerControlPresentation


class ResizingRuntimeFake:
    def __init__(self, worker_settings: WorkerSettingsService) -> None:
        self.worker_settings = worker_settings
        self.download_actual = 0
        self.upload_actual = 0
        self.resize_calls: list[tuple[str, int]] = []

    async def resize_download(self, workers: int) -> None:
        self.resize_calls.append(("download", workers))
        self.download_actual = workers

    async def resize_upload(self, workers: int) -> None:
        self.resize_calls.append(("upload", workers))
        self.upload_actual = workers

    async def snapshot(self) -> QueueRuntimeSnapshot:
        values = await self.worker_settings.get_values()
        return QueueRuntimeSnapshot(
            download=WorkerPoolSnapshot(
                values.download.current,
                self.download_actual,
                values.download.default,
                values.download.maximum,
            ),
            upload=WorkerPoolSnapshot(
                values.upload.current,
                self.upload_actual,
                values.upload.default,
                values.upload.maximum,
            ),
            download_jobs=QueueStatusCounts(),
            upload_jobs=QueueStatusCounts(),
        )


class CacheStatsFake:
    async def stats(self, *, telegram_bot_id: int | None = None) -> TelegramCacheStats:
        del telegram_bot_id
        return TelegramCacheStats(0, 0, 0)


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


async def _settings_values(database: Database) -> tuple[int, int]:
    async with database.transaction() as repositories:
        values = await repositories.runtime_settings.get()
        assert values is not None
        return values.download_workers, values.upload_workers


async def _control(
    database: Database,
    settings: Settings,
    *,
    owner_telegram_id: int,
) -> tuple[RuntimeWorkerControlService, WorkerSettingsService, ResizingRuntimeFake]:
    worker_settings = WorkerSettingsService(database, settings)
    values = await worker_settings.initialize()
    runtime = ResizingRuntimeFake(worker_settings)
    runtime.download_actual = values.download.current
    runtime.upload_actual = values.upload.current
    worker_settings.attach_resizer(runtime)
    control = RuntimeWorkerControlService(
        TelegramAuthorizationService(database, owner_id=owner_telegram_id),
        worker_settings,
        runtime,
    )
    return control, worker_settings, runtime


async def test_worker_control_permissions_mutations_bounds_persistence_and_no_side_effects(
    database: Database,
) -> None:
    settings = Settings(
        _env_file=None,
        download_workers_default=2,
        download_workers_max=4,
        upload_workers_default=3,
        upload_workers_max=5,
    )
    user_id = await _create_user(database, 1001)
    admin_id = await _create_user(database, 1002, UserRole.ADMIN)
    owner_id = await _create_user(database, 1003, UserRole.OWNER)
    stale_owner_id = await _create_user(database, 1004, UserRole.OWNER)
    control, _, runtime = await _control(database, settings, owner_telegram_id=1003)
    authorization = TelegramAuthorizationService(database, owner_id=1003)

    admin_access = await authorization.get_access_context(admin_id)
    owner_access = await authorization.get_access_context(owner_id)
    user_access = await authorization.get_access_context(user_id)
    stale_access = await authorization.get_access_context(stale_owner_id)
    assert AdminPermission.WORKERS_MANAGE in admin_access.permissions
    assert AdminPermission.OWNER_ONLY not in admin_access.permissions
    assert AdminPermission.WORKERS_MANAGE in owner_access.permissions
    assert AdminPermission.OWNER_ONLY in owner_access.permissions
    assert AdminPermission.WORKERS_MANAGE not in user_access.permissions
    assert not stale_access.permissions

    for actor_id in (user_id, stale_owner_id):
        with pytest.raises(AuthorizationError):
            await control.get_snapshot(actor_id)
        with pytest.raises(AuthorizationError):
            await control.adjust_download_workers(actor_id, 1)
        with pytest.raises(AuthorizationError):
            await control.reset_upload_workers(actor_id)
    assert await _settings_values(database) == (2, 3)

    with patch.object(ProviderResolver, "resolve", new_callable=AsyncMock) as provider_probe:
        assert (await control.adjust_download_workers(admin_id, 1)).desired == 3
        assert (await control.adjust_download_workers(admin_id, -1)).desired == 2
        assert (await control.adjust_upload_workers(admin_id, 1)).desired == 4
        assert (await control.adjust_upload_workers(admin_id, -1)).desired == 3
        provider_probe.assert_not_awaited()
    assert runtime.resize_calls == [
        ("download", 3),
        ("download", 2),
        ("upload", 4),
        ("upload", 3),
    ]

    await control.set_download_workers(admin_id, 4)
    reset = await control.reset_download_workers(admin_id)
    assert reset.status is WorkerMutationStatus.UPDATED
    assert reset.desired == settings.download_workers_default
    assert await _settings_values(database) == (2, 3)

    minimum = await control.adjust_download_workers(admin_id, -1)
    assert minimum.status is WorkerMutationStatus.UPDATED
    minimum = await control.adjust_download_workers(admin_id, -1)
    assert minimum.status is WorkerMutationStatus.MINIMUM_REACHED
    assert minimum.desired == 1
    await control.set_download_workers(admin_id, settings.download_workers_max)
    maximum = await control.adjust_download_workers(owner_id, 1)
    assert maximum.status is WorkerMutationStatus.MAXIMUM_REACHED
    assert maximum.desired == settings.download_workers_max

    for invalid in (0, settings.download_workers_max + 1):
        with pytest.raises(WorkerLimitError):
            await control.set_download_workers(owner_id, invalid)
    with pytest.raises(WorkerLimitError):
        await control.set_upload_workers(admin_id, 0)
    assert (await _settings_values(database))[0] == settings.download_workers_max

    before_upload = (await _settings_values(database))[1]
    await control.adjust_download_workers(admin_id, -1)
    assert (await _settings_values(database))[1] == before_upload
    before_download = (await _settings_values(database))[0]
    await control.adjust_upload_workers(owner_id, 1)
    assert (await _settings_values(database))[0] == before_download

    now = datetime.now(UTC)
    async with database.transaction() as repositories:
        track = await repositories.tracks.create_track(title="Active", artist="Worker")
    async with database.transaction() as repositories:
        active_download = await repositories.download_jobs.submit(
            track_id=track.id,
            quality_profile=QualityProfile.MP3_320,
            max_active=10,
            now=now,
        )
    async with database.transaction() as repositories:
        handoff_download = await repositories.download_jobs.submit(
            track_id=track.id,
            quality_profile=QualityProfile.MP3_128,
            max_active=10,
            now=now + timedelta(milliseconds=1),
        )
    async with database.transaction() as repositories:
        claimed_download = await repositories.download_jobs.claim(
            worker_id="active-download",
            now=now + timedelta(seconds=1),
            lease_expires_at=now + timedelta(minutes=5),
        )
        assert claimed_download is not None and claimed_download.id == active_download.id
    async with database.transaction() as repositories:
        claimed_handoff = await repositories.download_jobs.claim(
            worker_id="handoff-download",
            now=now + timedelta(seconds=1),
            lease_expires_at=now + timedelta(minutes=5),
        )
        assert claimed_handoff is not None and claimed_handoff.id == handoff_download.id
    async with database.transaction() as repositories:
        upload = await repositories.download_jobs.handoff(
            job_id=claimed_handoff.id,
            worker_id="handoff-download",
            artifact_job_id="active-upload-artifact",
            artifact_path="active-upload-artifact/output/final.mp3",
            now=now + timedelta(seconds=2),
        )
        assert upload is not None
    async with database.transaction() as repositories:
        active_upload = await repositories.upload_jobs.claim(
            worker_id="active-upload",
            now=now + timedelta(seconds=3),
            lease_expires_at=now + timedelta(minutes=5),
        )
        assert active_upload is not None

    await control.adjust_download_workers(admin_id, -1)
    await control.adjust_upload_workers(owner_id, -1)
    async with database.transaction() as repositories:
        unchanged_download = await repositories.download_jobs.get(active_download.id)
        unchanged_upload = await repositories.upload_jobs.get(active_upload.id)
        assert unchanged_download is not None
        assert unchanged_download.status is QueueJobStatus.RUNNING
        assert not unchanged_download.cancel_requested
        assert unchanged_upload is not None
        assert unchanged_upload.status is QueueJobStatus.RUNNING
        assert not unchanged_upload.cancel_requested

    persisted = await WorkerSettingsService(database, settings).get_values()
    assert persisted.download.current == before_download - 1
    assert persisted.upload.current == before_upload

    await _set_role(database, admin_id, UserRole.USER)
    with pytest.raises(AuthorizationError):
        await control.adjust_download_workers(admin_id, 1)
    await _set_role(database, user_id, UserRole.ADMIN)
    assert (
        await control.adjust_download_workers(user_id, 1)
    ).status is WorkerMutationStatus.UPDATED
    with pytest.raises(AuthorizationError):
        await AdministratorManagementService(
            database, authorization, owner_id=1003
        ).list_administrators(user_id)


async def test_relative_worker_adjustments_are_race_safe(database: Database) -> None:
    settings = Settings(
        _env_file=None,
        download_workers_default=2,
        download_workers_max=6,
        upload_workers_default=2,
        upload_workers_max=6,
    )
    admin_a = await _create_user(database, 2001, UserRole.ADMIN)
    admin_b = await _create_user(database, 2002, UserRole.ADMIN)
    control, _, _ = await _control(database, settings, owner_telegram_id=2999)

    await asyncio.gather(
        control.adjust_download_workers(admin_a, 1),
        control.adjust_download_workers(admin_b, 1),
    )
    assert (await _settings_values(database))[0] == 4

    await control.set_download_workers(admin_a, 5)
    await asyncio.gather(
        control.adjust_download_workers(admin_a, -1),
        control.adjust_download_workers(admin_b, -1),
    )
    assert (await _settings_values(database))[0] == 3

    await control.set_download_workers(admin_a, 5)
    boundary = await asyncio.gather(
        control.adjust_download_workers(admin_a, 1),
        control.adjust_download_workers(admin_b, 1),
    )
    assert (await _settings_values(database))[0] == 6
    assert {result.status for result in boundary} == {
        WorkerMutationStatus.UPDATED,
        WorkerMutationStatus.MAXIMUM_REACHED,
    }

    await control.set_download_workers(admin_a, 2)
    lower_boundary = await asyncio.gather(
        control.adjust_download_workers(admin_a, -1),
        control.adjust_download_workers(admin_b, -1),
    )
    assert (await _settings_values(database))[0] == 1
    assert {result.status for result in lower_boundary} == {
        WorkerMutationStatus.UPDATED,
        WorkerMutationStatus.MINIMUM_REACHED,
    }

    await control.set_download_workers(admin_a, 2)
    await control.set_upload_workers(admin_a, 2)
    await asyncio.gather(
        control.adjust_download_workers(admin_a, 1),
        control.adjust_upload_workers(admin_b, 1),
    )
    assert await _settings_values(database) == (3, 3)

    await control.set_download_workers(admin_a, 4)
    await asyncio.gather(
        control.reset_download_workers(admin_a),
        control.adjust_download_workers(admin_b, 1),
    )
    assert (await _settings_values(database))[0] in {2, 3}

    # Repository locking also protects callers that do not share one in-memory service lock.
    settings_a = WorkerSettingsService(database, settings)
    settings_b = WorkerSettingsService(database, settings)
    await settings_a.set_download_workers(2)
    await asyncio.gather(
        settings_a.adjust_download_workers(1),
        settings_b.adjust_download_workers(1),
    )
    assert (await _settings_values(database))[0] == 4


@dataclass(slots=True)
class _PoolJob:
    id: int


class BlockingPoolBackend:
    def __init__(self, jobs: int) -> None:
        self.wake_event = asyncio.Event()
        self.jobs = [_PoolJob(index) for index in range(jobs)]
        self.started = 0
        self.completed = 0
        self.cancelled = 0
        self.release = asyncio.Event()

    async def claim(self, worker_id: str) -> _PoolJob | None:
        del worker_id
        return self.jobs.pop(0) if self.jobs else None

    async def process(self, job: object, worker_id: str) -> None:
        del job, worker_id
        self.started += 1
        try:
            await self.release.wait()
            self.completed += 1
        except asyncio.CancelledError:
            self.cancelled += 1
            raise

    async def heartbeat(self, job_id: int, worker_id: str) -> bool:
        del job_id, worker_id
        return True


async def _wait_until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(3):
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


async def test_control_uses_queue_manager_for_live_convergence_and_graceful_downscale(
    database: Database, tmp_path: Path
) -> None:
    settings = Settings(
        _env_file=None,
        download_workers_default=2,
        download_workers_max=4,
        upload_workers_default=2,
        upload_workers_max=4,
    )
    admin_id = await _create_user(database, 2501, UserRole.ADMIN)
    worker_settings = WorkerSettingsService(database, settings)
    download_backend = BlockingPoolBackend(3)
    upload_backend = BlockingPoolBackend(3)
    artifacts = DownloadArtifactManager(tmp_path / "artifacts")
    manager = QueueManager(  # type: ignore[arg-type]
        settings,
        worker_settings,
        DownloadQueueService(database, max_size=10),
        UploadQueueService(database, artifacts),
        download_backend,
        upload_backend,
        reconcile_seconds=0.01,
    )
    control = RuntimeWorkerControlService(
        TelegramAuthorizationService(database, owner_id=None),
        worker_settings,
        manager,
    )

    await manager.start()
    try:
        await _wait_until(lambda: download_backend.started == 2)
        await _wait_until(lambda: upload_backend.started == 2)

        await control.set_download_workers(admin_id, 3)
        await control.set_upload_workers(admin_id, 3)
        await _wait_until(lambda: download_backend.started == 3)
        await _wait_until(lambda: upload_backend.started == 3)
        upscaled = await control.get_snapshot(admin_id)
        assert upscaled.download.desired_workers == upscaled.download.actual_workers == 3
        assert upscaled.upload.desired_workers == upscaled.upload.actual_workers == 3

        download_downscale = await control.set_download_workers(admin_id, 1)
        upload_downscale = await control.set_upload_workers(admin_id, 1)
        assert download_downscale.snapshot.download.desired_workers == 1
        assert download_downscale.snapshot.download.actual_workers == 3
        assert upload_downscale.snapshot.upload.desired_workers == 1
        assert upload_downscale.snapshot.upload.actual_workers == 3
        assert download_backend.cancelled == upload_backend.cancelled == 0

        download_backend.release.set()
        upload_backend.release.set()
        await _wait_until(lambda: manager._download_pool.actual == 1)
        assert manager._upload_pool is not None
        await _wait_until(
            lambda: manager._upload_pool is not None and manager._upload_pool.actual == 1
        )
        converged = await control.get_snapshot(admin_id)
        assert converged.download.desired_workers == converged.download.actual_workers == 1
        assert converged.upload.desired_workers == converged.upload.actual_workers == 1
        assert download_backend.completed == upload_backend.completed == 3
        assert download_backend.cancelled == upload_backend.cancelled == 0
    finally:
        download_backend.release.set()
        upload_backend.release.set()
        await manager.stop(1)


def _callback_update(
    update_id: int,
    telegram_id: int,
    data: str,
    *,
    chat_type: ChatType = ChatType.PRIVATE,
) -> Update:
    user = TgUser(id=telegram_id, is_bot=False, first_name="User", language_code="en")
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=f"callback-{update_id}",
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


async def test_worker_callbacks_reauthorize_reject_forgery_and_fallback_after_edit_failure(
    database: Database,
) -> None:
    settings = Settings(
        _env_file=None,
        download_workers_default=2,
        download_workers_max=6,
        upload_workers_default=3,
        upload_workers_max=6,
    )
    user_id = await _create_user(database, 3001)
    admin_id = await _create_user(database, 3002, UserRole.ADMIN)
    await _create_user(database, 3003, UserRole.OWNER)
    await _create_user(database, 3004, UserRole.OWNER)
    control, worker_settings, runtime = await _control(database, settings, owner_telegram_id=3003)
    authorization = TelegramAuthorizationService(database, owner_id=3003)
    i18n = LocalizationService(("en", "ru"), "en")
    users = TelegramUserService(database, i18n, owner_id=3003)
    overview = AdminOverviewService(
        database,
        authorization,
        runtime,
        CacheStatsFake(),
        telegram_bot_id=900,
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_admin_router(
            AdminHandlerDependencies(
                users,
                overview,
                AdminPresentation(i18n),
                AdministratorManagementService(database, authorization, owner_id=3003),
                AdminManagementPresentation(i18n),
                control,
                WorkerControlPresentation(i18n),
            )
        )
    )
    bot = Bot("123456:TEST_TOKEN")

    try:
        with patch.object(Bot, "__call__", new_callable=AsyncMock, return_value=True) as api:
            await dispatcher.feed_update(bot, _callback_update(1, 3001, "adm3:d:+"))
            assert isinstance(api.await_args.args[0], AnswerCallbackQuery)
            assert api.await_args.args[0].show_alert
            assert await _settings_values(database) == (2, 3)

            await dispatcher.feed_update(bot, _callback_update(2, 3004, "adm3:u:+"))
            assert isinstance(api.await_args.args[0], AnswerCallbackQuery)
            assert await _settings_values(database) == (2, 3)

            await dispatcher.feed_update(bot, _callback_update(3, 3002, "adm3:w"))
            assert isinstance(api.await_args.args[0], AnswerCallbackQuery)
            assert any(
                isinstance(call.args[0], EditMessageText) and "Runtime workers" in call.args[0].text
                for call in api.await_args_list
            )

            await dispatcher.feed_update(bot, _callback_update(4, 3002, "adm3:d:+"))
            assert (await worker_settings.get_values()).download.current == 3
            await dispatcher.feed_update(bot, _callback_update(5, 3002, "adm3:u:+"))
            assert await _settings_values(database) == (3, 4)

            await _set_role(database, admin_id, UserRole.USER)
            await dispatcher.feed_update(bot, _callback_update(6, 3002, "adm3:d:+"))
            assert await _settings_values(database) == (3, 4)
            assert isinstance(api.await_args.args[0], AnswerCallbackQuery)
            assert api.await_args.args[0].show_alert

            await _set_role(database, user_id, UserRole.ADMIN)
            await dispatcher.feed_update(bot, _callback_update(7, 3001, "adm3:d:+"))
            assert await _settings_values(database) == (4, 4)

            await dispatcher.feed_update(bot, _callback_update(8, 3001, "adm3:x:+"))
            assert await _settings_values(database) == (4, 4)
            assert isinstance(api.await_args.args[0], AnswerCallbackQuery)
            assert api.await_args.args[0].show_alert

            await dispatcher.feed_update(
                bot,
                _callback_update(9, 3001, "adm3:d:+", chat_type=ChatType.GROUP),
            )
            assert await _settings_values(database) == (4, 4)

            await dispatcher.feed_update(bot, _callback_update(10, 3003, "adm3:u:-"))
            assert await _settings_values(database) == (4, 3)

            with patch.object(
                worker_settings,
                "adjust_download_workers",
                new_callable=AsyncMock,
                side_effect=RuntimeError("persistence failed"),
            ):
                await dispatcher.feed_update(bot, _callback_update(11, 3001, "adm3:d:+"))
            assert await _settings_values(database) == (4, 3)
            assert isinstance(api.await_args.args[0], AnswerCallbackQuery)
            assert "Unable to update" in api.await_args.args[0].text

        async def fail_edits(method: object, **kwargs: object) -> bool:
            del kwargs
            if isinstance(method, EditMessageText):
                raise TelegramBadRequest(method=method, message="edit failed")
            return True

        with patch.object(Bot, "__call__", new_callable=AsyncMock) as api:
            api.side_effect = fail_edits
            await dispatcher.feed_update(bot, _callback_update(12, 3001, "adm3:d:+"))
            assert (await worker_settings.get_values()).download.current == 5
            assert any(isinstance(call.args[0], SendMessage) for call in api.await_args_list)
            assert isinstance(api.await_args.args[0], AnswerCallbackQuery)
    finally:
        await bot.session.close()
