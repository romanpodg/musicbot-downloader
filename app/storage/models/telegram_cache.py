"""Durable Telegram-backed completed-result cache metadata."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

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
    DownloadPlanOperation,
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    QualityProfile,
    TelegramCacheStatus,
    TelegramMediaKind,
)
from app.storage.models.base import Base, TimestampMixin, UTCDateTime


def _enum(enum_type: type[StrEnum], name: str, length: int) -> Enum:
    return Enum(
        enum_type,
        values_callable=lambda values: [member.value for member in values],
        name=name,
        native_enum=False,
        length=length,
        create_constraint=False,
    )


class TelegramFileCache(TimestampMixin, Base):
    __tablename__ = "telegram_file_cache"
    __table_args__ = (
        CheckConstraint(
            "quality_profile IN ('MP3_128', 'MP3_320', 'AAC_256', 'LOSSLESS')",
            name="ck_telegram_file_cache_quality_profile",
        ),
        CheckConstraint(
            "telegram_media_kind IN ('AUDIO', 'DOCUMENT')",
            name="ck_telegram_file_cache_media_kind",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'INVALID')",
            name="ck_telegram_file_cache_status",
        ),
        CheckConstraint("file_size_bytes > 0", name="ck_telegram_file_cache_file_size"),
        CheckConstraint(
            "(status = 'ACTIVE' AND invalidated_at IS NULL) OR "
            "(status = 'INVALID' AND invalidated_at IS NOT NULL)",
            name="ck_telegram_file_cache_invalidation",
        ),
        UniqueConstraint(
            "telegram_bot_id",
            "track_id",
            "quality_profile",
            name="uq_telegram_file_cache_key",
        ),
        Index("ix_telegram_file_cache_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_bot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    track_id: Mapped[int] = mapped_column(
        ForeignKey("tracks.id", ondelete="RESTRICT"), nullable=False
    )
    quality_profile: Mapped[QualityProfile] = mapped_column(
        _enum(QualityProfile, "qualityprofile", 16), nullable=False
    )
    telegram_file_id: Mapped[str] = mapped_column(String(512), nullable=False)
    telegram_file_unique_id: Mapped[str] = mapped_column(String(512), nullable=False)
    telegram_media_kind: Mapped[TelegramMediaKind] = mapped_column(
        _enum(TelegramMediaKind, "telegrammediakind", 16), nullable=False
    )
    cache_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cache_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    source_track_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("track_sources.id", ondelete="SET NULL")
    )
    source_provider: Mapped[MusicProviderName] = mapped_column(
        _enum(MusicProviderName, "musicprovidername", 32), nullable=False
    )
    source_provider_track_id: Mapped[str] = mapped_column(String(512), nullable=False)
    operation: Mapped[DownloadPlanOperation] = mapped_column(
        _enum(DownloadPlanOperation, "downloadplanoperation", 16), nullable=False
    )
    transcoded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_codec: Mapped[NativeCodec | None] = mapped_column(_enum(NativeCodec, "nativecodec", 16))
    source_container: Mapped[NativeContainer | None] = mapped_column(
        _enum(NativeContainer, "nativecontainer", 16)
    )
    source_bitrate_kbps: Mapped[int | None] = mapped_column(Integer)
    output_codec: Mapped[NativeCodec | None] = mapped_column(_enum(NativeCodec, "nativecodec", 16))
    output_container: Mapped[NativeContainer | None] = mapped_column(
        _enum(NativeContainer, "nativecontainer", 16)
    )
    output_bitrate_kbps: Mapped[int | None] = mapped_column(Integer)
    sample_rate_hz: Mapped[int | None] = mapped_column(Integer)
    bit_depth: Mapped[int | None] = mapped_column(Integer)
    channels: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    encoder: Mapped[str | None] = mapped_column(String(64))

    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[TelegramCacheStatus] = mapped_column(
        _enum(TelegramCacheStatus, "telegramcachestatus", 16), nullable=False
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    invalid_reason_code: Mapped[str | None] = mapped_column(String(64))
