"""Durable Stage 9 Telegram user request and delivery outbox."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import QualityProfile, TelegramDeliveryStatus
from app.storage.models.base import Base, TimestampMixin, UTCDateTime, utc_now


def _enum(enum_type: type[QualityProfile] | type[TelegramDeliveryStatus], name: str) -> Enum:
    return Enum(
        enum_type,
        values_callable=lambda values: [member.value for member in values],
        name=name,
        native_enum=False,
        length=16 if enum_type is QualityProfile else 24,
        create_constraint=False,
    )


class TelegramDeliveryRequest(TimestampMixin, Base):
    __tablename__ = "telegram_delivery_requests"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_telegram_delivery_attempt_count"),
        CheckConstraint("repair_count BETWEEN 0 AND 1", name="ck_telegram_delivery_repair_count"),
        CheckConstraint(
            "quality_profile IS NULL OR quality_profile IN "
            "('MP3_128', 'MP3_320', 'AAC_256', 'LOSSLESS')",
            name="ck_telegram_delivery_quality_profile",
        ),
        CheckConstraint(
            "status IN ('AWAITING_QUALITY', 'AWAITING_ACTION', "
            "'AWAITING_TRACK_QUALITY', 'QUEUED', 'WAITING', 'SENDING', "
            "'DELIVERED', 'FAILED', 'CANCELLED')",
            name="ck_telegram_delivery_status",
        ),
        CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)",
            name="ck_telegram_delivery_lease_pair",
        ),
        CheckConstraint(
            "(source_message_id IS NOT NULL AND album_item_id IS NULL) OR "
            "(source_message_id IS NULL AND album_item_id IS NOT NULL)",
            name="ck_telegram_delivery_origin",
        ),
        UniqueConstraint(
            "telegram_bot_id",
            "telegram_chat_id",
            "source_message_id",
            name="uq_telegram_delivery_message",
        ),
        UniqueConstraint("album_item_id", name="uq_telegram_delivery_album_item"),
        Index(
            "ix_telegram_delivery_claim",
            "status",
            "available_at",
            "created_at",
            "id",
        ),
        Index("ix_telegram_delivery_subscriber", "subscriber_id"),
        Index("ix_telegram_delivery_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_bot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_message_id: Mapped[int | None] = mapped_column(BigInteger)
    album_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("telegram_album_items.id", ondelete="RESTRICT")
    )
    track_id: Mapped[int] = mapped_column(
        ForeignKey("tracks.id", ondelete="RESTRICT"), nullable=False
    )
    quality_profile: Mapped[QualityProfile | None] = mapped_column(
        _enum(QualityProfile, "qualityprofile")
    )
    subscriber_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("job_subscribers.id", ondelete="SET NULL")
    )
    download_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("download_jobs.id", ondelete="SET NULL")
    )
    cache_id: Mapped[int | None] = mapped_column(
        ForeignKey("telegram_file_cache.id", ondelete="SET NULL")
    )
    status: Mapped[TelegramDeliveryStatus] = mapped_column(
        _enum(TelegramDeliveryStatus, "telegramdeliverystatus"), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    repair_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    delivered_message_id: Mapped[int | None] = mapped_column(BigInteger)
    card_message_id: Mapped[int | None] = mapped_column(BigInteger)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
