from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.application.download import DownloadService, DownloadTrackUseCase
from app.core.delivery_targets import ChannelTarget, GroupChatTarget, PrivateUserTarget
from app.core.download import (
    DownloadDeliveryTarget,
    DownloadRequest,
    DownloadSubmission,
    DownloadSubmissionState,
)
from app.core.enums import MusicProviderName
from app.core.recognition import RecognitionDecision, RecognitionResult, TrackCandidate
from app.core.search import Artist, Track
from app.core.telegram_context import (
    ChannelBinding,
    ChannelBindingStatus,
    ChatPolicy,
    DeliveryMode,
    TelegramChatType,
    TelegramContext,
)
from app.services.telegram_context import (
    ChatAccessOutcome,
    ChatContextAccessService,
    DeliveryTargetResolver,
)


def _context(chat_id: int, chat_type: TelegramChatType) -> TelegramContext:
    return TelegramContext(19001, chat_id, chat_type)


def _track() -> Track:
    return Track(
        id="search:spotify:stage19",
        title="Stage Nineteen",
        artists=(Artist("Music Bot"),),
        provider=MusicProviderName.SPOTIFY,
        provider_track_id="stage19",
    )


def _recognition() -> RecognitionResult:
    return RecognitionResult(TrackCandidate(_track(), "spotify"), 0.95, RecognitionDecision.ACCEPT)


def test_telegram_context_policy_binding_and_targets_are_validated() -> None:
    private = _context(19001, TelegramChatType.PRIVATE)
    assert private.user_id == 19001
    assert ChatPolicy(-19002, True, DeliveryMode.CHAT).delivery_mode is DeliveryMode.CHAT
    assert ChannelBinding(-10019003, ChannelBindingStatus.CONNECTED).channel_id == -10019003
    assert PrivateUserTarget(19001).chat_id == 19001
    assert GroupChatTarget(-19002).chat_id == -19002
    assert ChannelTarget(-10019003).chat_id == -10019003
    with pytest.raises(ValueError, match="must not be zero"):
        TelegramContext(19001, 0, TelegramChatType.PRIVATE)
    with pytest.raises(ValueError, match="must not be zero"):
        ChannelBinding(0, ChannelBindingStatus.CONNECTED)


def test_delivery_target_resolver_keeps_routing_rules_outside_delivery() -> None:
    resolver = DeliveryTargetResolver()
    private = _context(19001, TelegramChatType.PRIVATE)
    group = _context(-19002, TelegramChatType.GROUP)
    channel = _context(-10019003, TelegramChatType.CHANNEL)

    assert resolver.resolve(
        private, ChatPolicy(19001, True, DeliveryMode.USER)
    ) == PrivateUserTarget(19001)
    assert resolver.resolve(
        group, ChatPolicy(-19002, True, DeliveryMode.USER)
    ) == PrivateUserTarget(19001)
    assert resolver.resolve(group, ChatPolicy(-19002, True, DeliveryMode.CHAT)) == GroupChatTarget(
        -19002
    )
    assert resolver.resolve(channel, ChatPolicy(-10019003, True, DeliveryMode.CHAT)) is None
    assert resolver.resolve(
        channel,
        ChatPolicy(-10019003, True, DeliveryMode.CHAT),
        ChannelBinding(-10019003, ChannelBindingStatus.CONNECTED),
    ) == ChannelTarget(-10019003)
    assert (
        resolver.resolve(
            channel,
            ChatPolicy(-10019003, True, DeliveryMode.CHAT),
            ChannelBinding(-10019003, ChannelBindingStatus.NO_PERMISSION),
        )
        is None
    )


class _Resolver:
    async def resolve_track_id(self, track: Track) -> int:
        return 19


class _SubmissionPort:
    async def submit(
        self,
        request: DownloadRequest,
        *,
        canonical_track_id: int,
        target: DownloadDeliveryTarget,
    ) -> DownloadSubmission:
        return DownloadSubmission(request, canonical_track_id, 19, DownloadSubmissionState.QUEUED)


async def test_confirmation_rejects_cross_chat_callbacks_and_expired_contexts() -> None:
    now = datetime(2026, 8, 25, tzinfo=UTC)

    def clock() -> datetime:
        return now

    service = DownloadService(
        DownloadTrackUseCase(_Resolver(), _SubmissionPort()),
        token_factory=lambda: "d" * 24,
        clock=clock,
        confirmation_ttl=timedelta(seconds=30),
    )
    group_context = _context(-19002, TelegramChatType.GROUP)
    confirmation = service.create_confirmation(context=group_context, result=_recognition())
    assert confirmation is not None
    assert (
        service.cancel(
            context=TelegramContext(19001, -19004, TelegramChatType.GROUP),
            token=confirmation.token,
        )
        is False
    )
    assert service.cancel(context=group_context, token=confirmation.token) is True

    expiring = service.create_confirmation(context=group_context, result=_recognition())
    assert expiring is not None
    now = now + timedelta(seconds=31)
    assert (
        await service.confirm(
            context=group_context,
            token=expiring.token,
            target=DownloadDeliveryTarget(19001, group_context, GroupChatTarget(-19002), 7),
        )
        is None
    )


class _PermissionChecker:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    async def can_send_messages(self, chat_id: int) -> bool:
        return self.allowed


async def test_chat_access_combines_ban_state_policy_and_bot_capability(database) -> None:  # type: ignore[no-untyped-def]
    user_id = 19020
    group_id = -19020
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(user_id)
    context = TelegramContext(user_id, group_id, TelegramChatType.SUPERGROUP)
    missing = await ChatContextAccessService(
        database, DeliveryTargetResolver(), _PermissionChecker(True)
    ).resolve(context, user)
    assert missing.outcome is ChatAccessOutcome.POLICY_MISSING

    async with database.transaction() as repositories:
        await repositories.telegram_context.upsert_chat_policy(
            ChatPolicy(group_id, False, DeliveryMode.CHAT)
        )
    disabled = await ChatContextAccessService(
        database, DeliveryTargetResolver(), _PermissionChecker(True)
    ).resolve(context, user)
    assert disabled.outcome is ChatAccessOutcome.DOWNLOADS_DISABLED

    async with database.transaction() as repositories:
        await repositories.telegram_context.upsert_chat_policy(
            ChatPolicy(group_id, True, DeliveryMode.CHAT)
        )
    no_bot_permission = await ChatContextAccessService(
        database, DeliveryTargetResolver(), _PermissionChecker(False)
    ).resolve(context, user)
    assert no_bot_permission.outcome is ChatAccessOutcome.BOT_PERMISSION_MISSING

    async with database.transaction() as repositories:
        stored_user = await repositories.users.get(user.id)
        assert stored_user is not None
        stored_user.is_banned = True
    banned = await ChatContextAccessService(
        database, DeliveryTargetResolver(), _PermissionChecker(True)
    ).resolve(context, user)
    assert banned.outcome is ChatAccessOutcome.USER_BANNED
