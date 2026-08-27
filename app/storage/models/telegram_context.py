"""Durable Stage 19 chat policy and channel binding records."""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Enum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.telegram_context import ChannelBindingStatus, DeliveryMode
from app.storage.models.base import Base, TimestampMixin


class TelegramChatPolicy(TimestampMixin, Base):
    __tablename__ = "telegram_chat_policies"
    __table_args__ = (
        CheckConstraint("delivery_mode IN ('USER', 'CHAT')", name="ck_chat_policy_mode"),
    )

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    allow_downloads: Mapped[bool] = mapped_column(Boolean, nullable=False)
    delivery_mode: Mapped[DeliveryMode] = mapped_column(
        Enum(
            DeliveryMode,
            values_callable=lambda values: [member.value for member in values],
            name="deliverymode",
            native_enum=False,
            length=8,
            create_constraint=False,
        ),
        nullable=False,
    )


class TelegramChannelBinding(TimestampMixin, Base):
    __tablename__ = "telegram_channel_bindings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CONNECTED', 'NO_PERMISSION', 'DISCONNECTED')",
            name="ck_channel_binding_status",
        ),
    )

    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    status: Mapped[ChannelBindingStatus] = mapped_column(
        Enum(
            ChannelBindingStatus,
            values_callable=lambda values: [member.value for member in values],
            name="channelbindingstatus",
            native_enum=False,
            length=16,
            create_constraint=False,
        ),
        nullable=False,
    )
