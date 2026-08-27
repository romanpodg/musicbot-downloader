"""Persistence operations for Stage 19 chat routing configuration."""

from __future__ import annotations

from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.telegram_context import ChannelBinding, ChatPolicy
from app.storage.models import TelegramChannelBinding, TelegramChatPolicy


class TelegramContextRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_chat_policy(self, chat_id: int) -> TelegramChatPolicy | None:
        return cast(TelegramChatPolicy | None, await self._session.get(TelegramChatPolicy, chat_id))

    async def upsert_chat_policy(self, policy: ChatPolicy) -> TelegramChatPolicy:
        stored = await self.get_chat_policy(policy.chat_id)
        if stored is None:
            stored = TelegramChatPolicy(
                chat_id=policy.chat_id,
                allow_downloads=policy.allow_downloads,
                delivery_mode=policy.delivery_mode,
            )
            self._session.add(stored)
        else:
            stored.allow_downloads = policy.allow_downloads
            stored.delivery_mode = policy.delivery_mode
        await self._session.flush()
        return stored

    async def get_channel_binding(self, channel_id: int) -> TelegramChannelBinding | None:
        return cast(
            TelegramChannelBinding | None,
            await self._session.get(TelegramChannelBinding, channel_id),
        )

    async def upsert_channel_binding(self, binding: ChannelBinding) -> TelegramChannelBinding:
        stored = await self.get_channel_binding(binding.channel_id)
        if stored is None:
            stored = TelegramChannelBinding(channel_id=binding.channel_id, status=binding.status)
            self._session.add(stored)
        else:
            stored.status = binding.status
        await self._session.flush()
        return stored
