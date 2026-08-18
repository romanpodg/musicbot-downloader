from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot, Dispatcher
from aiogram.enums import ChatType, MessageEntityType
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, MessageEntity, Update
from aiogram.types import User as TgUser

from app.core.enums import (
    AlbumRequestStatus,
    MusicProviderName,
    QualityProfile,
    TelegramDeliveryStatus,
    UserRole,
)
from app.core.models import (
    AlbumSnapshot,
    AlbumTrackSnapshot,
    QueueRuntimeSnapshot,
    QueueStatusCounts,
    SingleFlightSnapshot,
    SubscriberStatusCounts,
    TelegramCacheStats,
    WorkerPoolSnapshot,
)
from app.i18n import LocalizationService
from app.services.admin_management import AdministratorManagementService
from app.services.admin_overview import AdminOverviewService
from app.services.authorization import (
    AdminPermission,
    AuthorizationError,
    AuthorizationFailureCode,
    TelegramAuthorizationService,
)
from app.services.provider_resolution import ProviderResolver
from app.services.telegram_users import TelegramUserProfile, TelegramUserService
from app.storage import Database
from app.storage.models.base import utc_now
from app.telegram.admin_handlers import AdminHandlerDependencies, create_admin_router
from app.telegram.admin_management_presentation import AdminManagementPresentation
from app.telegram.admin_presentation import AdminPresentation


class QueueSnapshotFake:
    def __init__(self, snapshot: QueueRuntimeSnapshot) -> None:
        self.value = snapshot
        self.calls = 0

    async def snapshot(self) -> QueueRuntimeSnapshot:
        self.calls += 1
        return self.value


class CacheStatsFake:
    def __init__(self, stats: TelegramCacheStats) -> None:
        self.value = stats
        self.calls: list[int | None] = []

    async def stats(self, *, telegram_bot_id: int | None = None) -> TelegramCacheStats:
        self.calls.append(telegram_bot_id)
        return self.value


def _runtime_snapshot() -> QueueRuntimeSnapshot:
    return QueueRuntimeSnapshot(
        download=WorkerPoolSnapshot(4, 3, 2, 8),
        upload=WorkerPoolSnapshot(3, 2, 3, 10),
        download_jobs=QueueStatusCounts(queued=3, running=2, failed=1),
        upload_jobs=QueueStatusCounts(queued=1, running=1, failed=2),
        singleflight=SingleFlightSnapshot(
            active_flights=2,
            subscribers=SubscriberStatusCounts(waiting=11, ready=7),
        ),
    )


async def _create_user(database: Database, telegram_id: int, role: UserRole = UserRole.USER) -> int:
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(
            telegram_id, first_name=f"user-{telegram_id}", role=role
        )
        return user.id


async def _set_role(database: Database, telegram_id: int, role: UserRole) -> None:
    async with database.transaction() as repositories:
        user = await repositories.users.get_by_telegram_id(telegram_id)
        assert user is not None
        await repositories.users.set_role(user, role)


async def test_central_authorization_owner_invariants_and_fresh_roles(
    database: Database,
) -> None:
    user_id = await _create_user(database, 101)
    admin_id = await _create_user(database, 102, UserRole.ADMIN)
    owner_id = await _create_user(database, 103, UserRole.OWNER)
    stale_owner_id = await _create_user(database, 104, UserRole.OWNER)
    matching_stale_role_id = await _create_user(database, 105, UserRole.ADMIN)
    authorization = TelegramAuthorizationService(database, owner_id=103)

    user_context = await authorization.get_access_context(user_id)
    assert not user_context.can_view_admin
    with pytest.raises(AuthorizationError) as user_denial:
        await authorization.require_authoritative_owner(user_id)
    assert user_denial.value.code is AuthorizationFailureCode.OWNER_REQUIRED

    admin_context = await authorization.require_permission(
        admin_id, AdminPermission.ADMIN_PANEL_VIEW
    )
    assert admin_context.effective_role is UserRole.ADMIN
    assert not admin_context.is_authoritative_owner
    with pytest.raises(AuthorizationError) as admin_denial:
        await authorization.require_authoritative_owner(admin_id)
    assert admin_denial.value.code is AuthorizationFailureCode.OWNER_REQUIRED

    owner_context = await authorization.require_authoritative_owner(owner_id)
    assert owner_context.is_authoritative_owner
    assert owner_context.can_view_admin

    stale_context = await authorization.get_access_context(stale_owner_id)
    assert stale_context.effective_role is UserRole.OWNER
    assert not stale_context.is_authoritative_owner
    assert not stale_context.permissions
    with pytest.raises(AuthorizationError) as stale_denial:
        await authorization.require_permission(stale_owner_id, AdminPermission.ADMIN_PANEL_VIEW)
    assert stale_denial.value.code is AuthorizationFailureCode.OWNER_IDENTITY_MISMATCH

    matching_context = await authorization.get_access_context(matching_stale_role_id)
    assert matching_context.effective_role is UserRole.ADMIN
    assert not matching_context.is_authoritative_owner

    users = TelegramUserService(database, LocalizationService(("en", "ru"), "en"), owner_id=105)
    reconciled = await users.observe(TelegramUserProfile(105, None, "Owner", "en"))
    assert reconciled.role is UserRole.OWNER
    reconciled_authorization = TelegramAuthorizationService(database, owner_id=105)
    assert (
        await reconciled_authorization.require_authoritative_owner(reconciled.id)
    ).is_authoritative_owner

    await _set_role(database, 101, UserRole.ADMIN)
    assert (await authorization.get_access_context(user_id)).can_view_admin
    await _set_role(database, 102, UserRole.USER)
    assert not (await authorization.get_access_context(admin_id)).can_view_admin

    restarted_authorization = TelegramAuthorizationService(database, owner_id=103)
    assert (await restarted_authorization.get_access_context(user_id)).can_view_admin
    assert (
        await restarted_authorization.require_authoritative_owner(owner_id)
    ).is_authoritative_owner


async def test_admin_overview_uses_bounded_local_statistics_without_mutation_or_probes(
    database: Database,
) -> None:
    admin_id = await _create_user(database, 201, UserRole.ADMIN)
    snapshot = _runtime_snapshot()
    queues = QueueSnapshotFake(snapshot)
    cache = CacheStatsFake(TelegramCacheStats(4_231, 7, 999_999))

    async with database.transaction() as repositories:
        user = await repositories.users.get(admin_id)
        assert user is not None
        track = await repositories.tracks.create_track(title="Track", artist="Artist")
        delivery_statuses = (
            TelegramDeliveryStatus.QUEUED,
            TelegramDeliveryStatus.WAITING,
            TelegramDeliveryStatus.SENDING,
            TelegramDeliveryStatus.FAILED,
        )
        for index, status in enumerate(delivery_statuses, start=1):
            request = await repositories.telegram_delivery.create(
                telegram_bot_id=900,
                user_id=user.id,
                telegram_chat_id=201,
                source_message_id=index,
                track_id=track.id,
                quality_profile=QualityProfile.MP3_320,
                status=status,
                now=utc_now(),
            )
            request.status = status

        album_snapshot = AlbumSnapshot(
            provider=MusicProviderName.SPOTIFY,
            provider_album_id="album",
            source_url="https://example.test/album",
            title="Album",
            artist="Artist",
            tracks=(AlbumTrackSnapshot("track", 1),),
        )
        active = await repositories.telegram_album.create(
            telegram_bot_id=900,
            user_id=user.id,
            telegram_chat_id=201,
            source_message_id=10,
            snapshot=album_snapshot,
            quality_profile=QualityProfile.MP3_320,
            now=utc_now(),
        )
        completed = await repositories.telegram_album.create(
            telegram_bot_id=900,
            user_id=user.id,
            telegram_chat_id=201,
            source_message_id=11,
            snapshot=AlbumSnapshot(
                provider=MusicProviderName.SPOTIFY,
                provider_album_id="album-2",
                source_url="https://example.test/album-2",
                title="Album 2",
                artist="Artist",
                tracks=(AlbumTrackSnapshot("track-2", 1),),
            ),
            quality_profile=QualityProfile.MP3_320,
            now=utc_now(),
        )
        active.status = AlbumRequestStatus.PROCESSING
        completed.status = AlbumRequestStatus.COMPLETED

    overview_service = AdminOverviewService(
        database,
        TelegramAuthorizationService(database, owner_id=None),
        queues,
        cache,
        telegram_bot_id=900,
    )
    with patch.object(ProviderResolver, "resolve", new_callable=AsyncMock) as provider_resolution:
        result = await overview_service.get_overview(admin_id)
        provider_resolution.assert_not_awaited()

    assert result.overview.queues is snapshot
    assert result.overview.telegram_cache == TelegramCacheStats(4_231, 7, 999_999)
    assert result.overview.deliveries.waiting_or_queued == 2
    assert result.overview.deliveries.sending == 1
    assert result.overview.deliveries.failed == 1
    assert result.overview.albums.active == 1
    assert queues.calls == 1
    assert cache.calls == [900]

    async with database.transaction() as repositories:
        counts = await repositories.telegram_delivery.status_counts()
        assert sum(counts.values()) == 4
        assert await repositories.telegram_album.count_active() == 1


async def test_admin_router_denial_access_forgery_and_fresh_role_changes(
    database: Database,
) -> None:
    user_id = await _create_user(database, 301, UserRole.USER)
    admin_id = await _create_user(database, 302, UserRole.ADMIN)
    await _create_user(database, 303, UserRole.OWNER)
    fallback_target_id = await _create_user(database, 304, UserRole.USER)
    i18n = LocalizationService(("en", "ru"), "en")
    users = TelegramUserService(database, i18n, owner_id=303)
    queues = QueueSnapshotFake(_runtime_snapshot())
    authorization = TelegramAuthorizationService(database, owner_id=303)
    overview = AdminOverviewService(
        database,
        authorization,
        queues,
        CacheStatsFake(TelegramCacheStats(10, 1, 123)),
        telegram_bot_id=900,
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_admin_router(
            AdminHandlerDependencies(
                users,
                overview,
                AdminPresentation(i18n),
                AdministratorManagementService(database, authorization, owner_id=303),
                AdminManagementPresentation(i18n),
            )
        )
    )
    bot = Bot("123456:TEST_TOKEN")

    def message_update(
        update_id: int, telegram_id: int, *, chat_type: ChatType = ChatType.PRIVATE
    ) -> Update:
        text = "/admin"
        return Update(
            update_id=update_id,
            message=Message(
                message_id=update_id,
                date=datetime.now(UTC),
                chat=Chat(id=telegram_id, type=chat_type),
                from_user=TgUser(
                    id=telegram_id, is_bot=False, first_name="User", language_code="en"
                ),
                text=text,
                entities=[
                    MessageEntity(type=MessageEntityType.BOT_COMMAND, offset=0, length=len(text))
                ],
            ),
        )

    def callback_update(
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

    try:
        with patch.object(Bot, "__call__", new_callable=AsyncMock, return_value=True) as api:
            await dispatcher.feed_update(bot, message_update(1, 301))
            assert isinstance(api.await_args.args[0], SendMessage)
            assert "do not have access" in api.await_args.args[0].text
            assert queues.calls == 0

            await dispatcher.feed_update(bot, callback_update(2, 301, "adm1:refresh"))
            assert isinstance(api.await_args.args[0], AnswerCallbackQuery)
            assert api.await_args.args[0].show_alert
            assert queues.calls == 0

            await dispatcher.feed_update(bot, message_update(3, 302))
            assert isinstance(api.await_args.args[0], SendMessage)
            assert "Role: Administrator" in api.await_args.args[0].text
            assert queues.calls == 1

            await dispatcher.feed_update(bot, message_update(4, 302, chat_type=ChatType.GROUP))
            assert isinstance(api.await_args.args[0], SendMessage)
            assert "only in a private chat" in api.await_args.args[0].text
            assert queues.calls == 1

            await dispatcher.feed_update(bot, message_update(5, 303))
            assert isinstance(api.await_args.args[0], SendMessage)
            assert "Role: Owner" in api.await_args.args[0].text
            owner_markup = api.await_args.args[0].reply_markup
            assert owner_markup is not None
            owner_callbacks = [
                button.callback_data for row in owner_markup.inline_keyboard for button in row
            ]
            assert "adm2:l:0" in owner_callbacks
            assert queues.calls == 2

            await dispatcher.feed_update(bot, callback_update(6, 302, "adm2:l:0"))
            assert isinstance(api.await_args.args[0], AnswerCallbackQuery)
            assert api.await_args.args[0].show_alert
            assert queues.calls == 2

            await dispatcher.feed_update(bot, callback_update(7, 302, f"adm2:pc:{user_id}:0"))
            assert isinstance(api.await_args.args[0], AnswerCallbackQuery)
            async with database.transaction() as repositories:
                target = await repositories.users.get(user_id)
                assert target is not None
                assert target.role is UserRole.USER

            await dispatcher.feed_update(bot, callback_update(8, 301, "adm2:l:0"))
            assert isinstance(api.await_args.args[0], AnswerCallbackQuery)
            assert api.await_args.args[0].show_alert

            await dispatcher.feed_update(
                bot,
                callback_update(9, 303, "adm2:l:0", chat_type=ChatType.GROUP),
            )
            assert isinstance(api.await_args.args[0], AnswerCallbackQuery)
            assert api.await_args.args[0].show_alert

            await dispatcher.feed_update(bot, callback_update(10, 303, "adm2:l:0"))
            edits = [
                call.args[0]
                for call in api.await_args_list
                if call.args and isinstance(call.args[0], EditMessageText)
            ]
            assert "Administrators" in edits[-1].text
            assert edits[-1].reply_markup is not None
            assert any(
                "302" in button.text
                for row in edits[-1].reply_markup.inline_keyboard
                for button in row
            )

            await dispatcher.feed_update(bot, callback_update(11, 303, "adm2:a:0"))
            edits = [
                call.args[0]
                for call in api.await_args_list
                if call.args and isinstance(call.args[0], EditMessageText)
            ]
            assert "Add administrator" in edits[-1].text

            await dispatcher.feed_update(bot, callback_update(12, 303, f"adm2:p:{user_id}:0"))
            async with database.transaction() as repositories:
                target = await repositories.users.get(user_id)
                assert target is not None
                assert target.role is UserRole.USER
            edits = [
                call.args[0]
                for call in api.await_args_list
                if call.args and isinstance(call.args[0], EditMessageText)
            ]
            assert "Promote administrator?" in edits[-1].text

            calls_before_promotion = len(api.await_args_list)
            await dispatcher.feed_update(bot, callback_update(13, 303, f"adm2:pc:{user_id}:0"))
            assert not any(
                call.args and isinstance(call.args[0], SendMessage)
                for call in api.await_args_list[calls_before_promotion:]
            )
            async with database.transaction() as repositories:
                target = await repositories.users.get(user_id)
                assert target is not None
                assert target.role is UserRole.ADMIN

            await dispatcher.feed_update(bot, message_update(14, 301))
            assert isinstance(api.await_args.args[0], SendMessage)
            assert "Role: Administrator" in api.await_args.args[0].text
            assert queues.calls == 3

            await dispatcher.feed_update(bot, callback_update(15, 303, f"adm2:u:{admin_id}:0"))
            await dispatcher.feed_update(bot, callback_update(16, 303, f"adm2:r:{admin_id}:0"))
            async with database.transaction() as repositories:
                target = await repositories.users.get(admin_id)
                assert target is not None
                assert target.role is UserRole.ADMIN
            edits = [
                call.args[0]
                for call in api.await_args_list
                if call.args and isinstance(call.args[0], EditMessageText)
            ]
            assert "Remove administrator?" in edits[-1].text

            calls_before_demotion = len(api.await_args_list)
            await dispatcher.feed_update(bot, callback_update(17, 303, f"adm2:rc:{admin_id}:0"))
            assert not any(
                call.args and isinstance(call.args[0], SendMessage)
                for call in api.await_args_list[calls_before_demotion:]
            )
            async with database.transaction() as repositories:
                target = await repositories.users.get(admin_id)
                assert target is not None
                assert target.role is UserRole.USER

            await dispatcher.feed_update(bot, callback_update(18, 302, "adm1:refresh"))
            assert isinstance(api.await_args.args[0], AnswerCallbackQuery)
            assert api.await_args.args[0].show_alert
            assert queues.calls == 3

            queues.value = QueueRuntimeSnapshot(
                download=WorkerPoolSnapshot(4, 3, 2, 8),
                upload=WorkerPoolSnapshot(3, 2, 3, 10),
                download_jobs=QueueStatusCounts(queued=99),
                upload_jobs=QueueStatusCounts(),
                singleflight=SingleFlightSnapshot(0, SubscriberStatusCounts()),
            )
            await dispatcher.feed_update(bot, callback_update(19, 301, "adm1:refresh"))
            edit_calls = [
                call.args[0]
                for call in api.await_args_list
                if call.args and isinstance(call.args[0], EditMessageText)
            ]
            assert edit_calls
            assert "99 queued" in edit_calls[-1].text
            assert queues.calls == 4

            await dispatcher.feed_update(bot, callback_update(20, 301, "adm1:close"))
            assert isinstance(api.await_args.args[0], AnswerCallbackQuery)
            assert queues.calls == 4

            await dispatcher.feed_update(bot, callback_update(21, 301, "adm1:unknown"))
            assert isinstance(api.await_args.args[0], AnswerCallbackQuery)
            assert api.await_args.args[0].show_alert
            assert queues.calls == 4

            async def fail_edit(method: object) -> bool:
                if isinstance(method, EditMessageText):
                    raise TelegramBadRequest(method, "edit failed")
                return True

            api.side_effect = fail_edit
            calls_before_fallback = len(api.await_args_list)
            await dispatcher.feed_update(
                bot,
                callback_update(22, 303, f"adm2:pc:{fallback_target_id}:0"),
            )
            async with database.transaction() as repositories:
                target = await repositories.users.get(fallback_target_id)
                assert target is not None
                assert target.role is UserRole.ADMIN
            assert any(
                call.args and isinstance(call.args[0], SendMessage)
                for call in api.await_args_list[calls_before_fallback:]
            )
    finally:
        await bot.session.close()
