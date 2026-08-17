"""Durable provider-release snapshots and album orchestration state."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import (
    AlbumItemResolutionStatus,
    AlbumRequestStatus,
    MusicProviderName,
    QualityProfile,
)
from app.storage.models.base import Base, TimestampMixin, UTCDateTime, utc_now


def _enum(enum_type: type, name: str, length: int) -> Enum:
    return Enum(
        enum_type,
        values_callable=lambda values: [member.value for member in values],
        name=name,
        native_enum=False,
        length=length,
        create_constraint=False,
    )


class TelegramAlbumRequest(TimestampMixin, Base):
    __tablename__ = "telegram_album_requests"
    __table_args__ = (
        CheckConstraint("track_count BETWEEN 1 AND 500", name="ck_telegram_album_track_count"),
        CheckConstraint(
            "quality_profile IS NULL OR quality_profile IN "
            "('MP3_128', 'MP3_320', 'AAC_256', 'LOSSLESS')",
            name="ck_telegram_album_quality_profile",
        ),
        CheckConstraint(
            "status IN ('AWAITING_QUALITY', 'AWAITING_ACTION', 'AWAITING_ALBUM_QUALITY', "
            "'SELECTING_TRACKS', 'QUEUED', 'PROCESSING', 'COMPLETED', "
            "'PARTIALLY_FAILED', 'FAILED', 'CANCELLED')",
            name="ck_telegram_album_status",
        ),
        UniqueConstraint(
            "telegram_bot_id",
            "telegram_chat_id",
            "source_message_id",
            name="uq_telegram_album_message",
        ),
        Index("ix_telegram_album_claim", "status", "updated_at", "id"),
        Index("ix_telegram_album_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_bot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider: Mapped[MusicProviderName] = mapped_column(
        _enum(MusicProviderName, "musicprovider", 32), nullable=False
    )
    provider_album_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    artist: Mapped[str] = mapped_column(String(1024), nullable=False)
    release_date: Mapped[str | None] = mapped_column(String(32))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    track_count: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_profile: Mapped[QualityProfile | None] = mapped_column(
        _enum(QualityProfile, "qualityprofile", 16)
    )
    status: Mapped[AlbumRequestStatus] = mapped_column(
        _enum(AlbumRequestStatus, "telegramalbumrequeststatus", 32), nullable=False
    )
    card_message_id: Mapped[int | None] = mapped_column(BigInteger)
    completion_message_id: Mapped[int | None] = mapped_column(BigInteger)
    completion_notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error_code: Mapped[str | None] = mapped_column(String(64))


class TelegramAlbumItem(TimestampMixin, Base):
    __tablename__ = "telegram_album_items"
    __table_args__ = (
        CheckConstraint("position >= 1", name="ck_telegram_album_item_position"),
        CheckConstraint("attempt_count >= 0", name="ck_telegram_album_item_attempt_count"),
        CheckConstraint(
            "resolution_status IN ('PENDING', 'RESOLVING', 'ATTACHED', 'FAILED', 'CANCELLED')",
            name="ck_telegram_album_item_resolution_status",
        ),
        CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)",
            name="ck_telegram_album_item_lease_pair",
        ),
        UniqueConstraint("album_request_id", "position", name="uq_telegram_album_item_position"),
        Index(
            "ix_telegram_album_item_claim",
            "album_request_id",
            "selected",
            "resolution_status",
            "available_at",
            "position",
        ),
        Index("ix_telegram_album_item_track", "track_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    album_request_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_album_requests.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    disc_number: Mapped[int | None] = mapped_column(Integer)
    track_number: Mapped[int | None] = mapped_column(Integer)
    provider_track_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str | None] = mapped_column(String(1024))
    artist: Mapped[str | None] = mapped_column(String(1024))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    explicit: Mapped[bool | None] = mapped_column(Boolean)
    selected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolution_status: Mapped[AlbumItemResolutionStatus] = mapped_column(
        _enum(AlbumItemResolutionStatus, "telegramalbumitemstatus", 16), nullable=False
    )
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id", ondelete="RESTRICT"))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error_code: Mapped[str | None] = mapped_column(String(64))
