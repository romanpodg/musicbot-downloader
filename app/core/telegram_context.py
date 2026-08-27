"""Transport-neutral Telegram context and chat routing policy values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TelegramChatType(StrEnum):
    PRIVATE = "private"
    GROUP = "group"
    SUPERGROUP = "supergroup"
    CHANNEL = "channel"


class DeliveryMode(StrEnum):
    USER = "USER"
    CHAT = "CHAT"


class ChannelBindingStatus(StrEnum):
    CONNECTED = "CONNECTED"
    NO_PERMISSION = "NO_PERMISSION"
    DISCONNECTED = "DISCONNECTED"


@dataclass(frozen=True, slots=True)
class TelegramContext:
    """The actor and chat of one Telegram interaction, without aiogram types."""

    user_id: int
    chat_id: int
    chat_type: TelegramChatType

    def __post_init__(self) -> None:
        if self.user_id <= 0:
            raise ValueError("Telegram context user ID must be positive")
        if self.chat_id == 0:
            raise ValueError("Telegram context chat ID must not be zero")
        if not isinstance(self.chat_type, TelegramChatType):
            raise TypeError("Telegram context chat type must be TelegramChatType")


@dataclass(frozen=True, slots=True)
class ChatPolicy:
    """Durable chat-level download policy; it does not grant global roles."""

    chat_id: int
    allow_downloads: bool
    delivery_mode: DeliveryMode

    def __post_init__(self) -> None:
        if self.chat_id == 0:
            raise ValueError("Chat policy chat ID must not be zero")
        if not isinstance(self.allow_downloads, bool):
            raise TypeError("Chat policy allow_downloads must be bool")
        if not isinstance(self.delivery_mode, DeliveryMode):
            raise TypeError("Chat policy delivery mode must be DeliveryMode")


@dataclass(frozen=True, slots=True)
class ChannelBinding:
    """Explicit lifecycle state for a channel that the bot may deliver to."""

    channel_id: int
    status: ChannelBindingStatus

    def __post_init__(self) -> None:
        if self.channel_id == 0:
            raise ValueError("Channel binding ID must not be zero")
        if not isinstance(self.status, ChannelBindingStatus):
            raise TypeError("Channel binding status must be ChannelBindingStatus")


class ChatRateLimitPolicy:
    """Future extension seam; Stage 19 deliberately performs no throttling."""

    def allows(self, context: TelegramContext) -> bool:
        raise NotImplementedError
