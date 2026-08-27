"""Provider- and Telegram-type-neutral delivery destination values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DeliveryTargetType(StrEnum):
    PRIVATE_USER = "PRIVATE_USER"
    GROUP_CHAT = "GROUP_CHAT"
    CHANNEL = "CHANNEL"


@dataclass(frozen=True, slots=True)
class DeliveryTarget:
    """A completed artifact destination, independent from the requesting user."""

    chat_id: int
    target_type: DeliveryTargetType

    def __post_init__(self) -> None:
        if self.chat_id == 0:
            raise ValueError("Delivery target chat ID must not be zero")
        if not isinstance(self.target_type, DeliveryTargetType):
            raise TypeError("Delivery target type must be DeliveryTargetType")


class PrivateUserTarget(DeliveryTarget):
    def __init__(self, user_id: int) -> None:
        super().__init__(user_id, DeliveryTargetType.PRIVATE_USER)


class GroupChatTarget(DeliveryTarget):
    def __init__(self, chat_id: int) -> None:
        super().__init__(chat_id, DeliveryTargetType.GROUP_CHAT)


class ChannelTarget(DeliveryTarget):
    def __init__(self, channel_id: int) -> None:
        super().__init__(channel_id, DeliveryTargetType.CHANNEL)


def delivery_target_from_values(chat_id: int, target_type: DeliveryTargetType) -> DeliveryTarget:
    if target_type is DeliveryTargetType.PRIVATE_USER:
        return PrivateUserTarget(chat_id)
    if target_type is DeliveryTargetType.GROUP_CHAT:
        return GroupChatTarget(chat_id)
    if target_type is DeliveryTargetType.CHANNEL:
        return ChannelTarget(chat_id)
    raise ValueError("unsupported delivery target type")
