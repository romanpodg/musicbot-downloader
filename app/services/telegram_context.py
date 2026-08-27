"""Stage 19 context authorization and delivery-target routing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.core.delivery_targets import (
    ChannelTarget,
    DeliveryTarget,
    GroupChatTarget,
    PrivateUserTarget,
)
from app.core.telegram_context import (
    ChannelBinding,
    ChannelBindingStatus,
    ChatPolicy,
    DeliveryMode,
    TelegramChatType,
    TelegramContext,
)
from app.storage import Database
from app.storage.models import User


class ChatPermissionChecker(Protocol):
    """Verify that the bot can present and deliver into one non-private chat."""

    async def can_send_messages(self, chat_id: int) -> bool: ...


class ChatAccessOutcome(StrEnum):
    ALLOWED = "ALLOWED"
    USER_BANNED = "USER_BANNED"
    POLICY_MISSING = "POLICY_MISSING"
    DOWNLOADS_DISABLED = "DOWNLOADS_DISABLED"
    CHANNEL_UNBOUND = "CHANNEL_UNBOUND"
    CHANNEL_UNAVAILABLE = "CHANNEL_UNAVAILABLE"
    BOT_PERMISSION_MISSING = "BOT_PERMISSION_MISSING"


@dataclass(frozen=True, slots=True)
class ChatAccessResult:
    outcome: ChatAccessOutcome
    target: DeliveryTarget | None = None

    @property
    def allowed(self) -> bool:
        return self.outcome is ChatAccessOutcome.ALLOWED and self.target is not None


class DeliveryTargetResolver:
    """Pure routing policy: it has no downloader, queue, or Telegram client dependency."""

    def resolve(
        self,
        context: TelegramContext,
        policy: ChatPolicy,
        channel_binding: ChannelBinding | None = None,
    ) -> DeliveryTarget | None:
        if context.chat_id != policy.chat_id or not policy.allow_downloads:
            return None
        if context.chat_type is TelegramChatType.PRIVATE:
            return PrivateUserTarget(context.user_id)
        if context.chat_type in {TelegramChatType.GROUP, TelegramChatType.SUPERGROUP}:
            if policy.delivery_mode is DeliveryMode.USER:
                return PrivateUserTarget(context.user_id)
            return GroupChatTarget(context.chat_id)
        if context.chat_type is TelegramChatType.CHANNEL:
            if (
                channel_binding is None
                or channel_binding.channel_id != context.chat_id
                or channel_binding.status is not ChannelBindingStatus.CONNECTED
            ):
                return None
            return ChannelTarget(channel_binding.channel_id)
        return None


class ChatContextAccessService:
    """Freshly evaluate global user state plus separate chat-level policy for each action."""

    def __init__(
        self,
        database: Database,
        resolver: DeliveryTargetResolver,
        permission_checker: ChatPermissionChecker | None,
    ) -> None:
        self._database = database
        self._resolver = resolver
        self._permission_checker = permission_checker

    async def resolve(self, context: TelegramContext, user: User) -> ChatAccessResult:
        persisted_user, policy, binding = await self._routing_records(context, user.id)
        if (
            persisted_user is None
            or persisted_user.telegram_id != context.user_id
            or persisted_user.is_banned
        ):
            return ChatAccessResult(ChatAccessOutcome.USER_BANNED)
        if policy is None:
            return ChatAccessResult(ChatAccessOutcome.POLICY_MISSING)
        if not policy.allow_downloads:
            return ChatAccessResult(ChatAccessOutcome.DOWNLOADS_DISABLED)
        if context.chat_type is TelegramChatType.CHANNEL:
            if binding is None:
                return ChatAccessResult(ChatAccessOutcome.CHANNEL_UNBOUND)
            if binding.status is not ChannelBindingStatus.CONNECTED:
                return ChatAccessResult(ChatAccessOutcome.CHANNEL_UNAVAILABLE)

        target = self._resolver.resolve(context, policy, binding)
        if target is None:
            return ChatAccessResult(ChatAccessOutcome.DOWNLOADS_DISABLED)
        if context.chat_type is not TelegramChatType.PRIVATE:
            if self._permission_checker is None:
                return ChatAccessResult(ChatAccessOutcome.BOT_PERMISSION_MISSING)
            try:
                allowed = await self._permission_checker.can_send_messages(context.chat_id)
            except Exception:
                allowed = False
            if not allowed:
                return ChatAccessResult(ChatAccessOutcome.BOT_PERMISSION_MISSING)
        return ChatAccessResult(ChatAccessOutcome.ALLOWED, target)

    async def _routing_records(
        self, context: TelegramContext, user_id: int
    ) -> tuple[User | None, ChatPolicy | None, ChannelBinding | None]:
        async with self._database.transaction() as repositories:
            persisted_user = await repositories.users.get(user_id)
            stored_policy = (
                None
                if context.chat_type is TelegramChatType.PRIVATE
                else await repositories.telegram_context.get_chat_policy(context.chat_id)
            )
            stored_binding = (
                await repositories.telegram_context.get_channel_binding(context.chat_id)
                if context.chat_type is TelegramChatType.CHANNEL
                else None
            )
        if context.chat_type is TelegramChatType.PRIVATE:
            return persisted_user, ChatPolicy(context.chat_id, True, DeliveryMode.USER), None
        policy = (
            ChatPolicy(
                stored_policy.chat_id,
                stored_policy.allow_downloads,
                stored_policy.delivery_mode,
            )
            if stored_policy is not None
            else None
        )
        binding = (
            ChannelBinding(stored_binding.channel_id, stored_binding.status)
            if stored_binding is not None
            else None
        )
        return persisted_user, policy, binding
