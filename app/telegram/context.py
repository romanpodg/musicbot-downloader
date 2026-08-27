"""Aiogram-to-domain context adapter for Stage 19 routing."""

from __future__ import annotations

from app.core.telegram_context import TelegramChatType, TelegramContext


def telegram_context_from_values(
    user_id: int, chat_id: int, raw_chat_type: object
) -> TelegramContext | None:
    value = getattr(raw_chat_type, "value", raw_chat_type)
    try:
        return TelegramContext(user_id, chat_id, TelegramChatType(str(value)))
    except (TypeError, ValueError):
        return None
