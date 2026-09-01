"""Stage 23 durable collection batches and ordered membership snapshots."""

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
    BatchItemStatus,
    BatchSourceType,
    BatchStatus,
    DeliveryMode,
    FormatPreference,
    MusicProviderName,
    QualityPreference,
)
from app.storage.models.base import Base, TimestampMixin, UTCDateTime


def _enum(enum_type: type, length: int) -> Enum:
    return Enum(
        enum_type,
        values_callable=lambda values: [v.value for v in values],
        native_enum=False,
        length=length,
        create_constraint=False,
    )


class BatchDownloadRequest(TimestampMixin, Base):
    __tablename__ = "batch_download_requests"
    __table_args__ = (
        UniqueConstraint("confirmation_id", name="uq_batch_download_requests_confirmation"),
        UniqueConstraint(
            "parent_batch_id", "retry_generation", name="uq_batch_download_requests_retry"
        ),
        CheckConstraint("total_items >= 1", name="ck_batch_download_requests_total_items"),
        CheckConstraint(
            "source_type IN ('album','playlist')", name="ck_batch_download_requests_source_type"
        ),
        CheckConstraint(
            "status IN ('PENDING','EXPANDING','ACTIVE','COMPLETED','PARTIAL','FAILED','CANCELLED')",
            name="ck_batch_download_requests_status",
        ),
        Index("ix_batch_download_requests_user_created", "requester_user_id", "created_at"),
        Index("ix_batch_download_requests_status", "status"),
        Index("ix_batch_download_requests_parent", "parent_batch_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    requester_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    confirmation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[BatchSourceType] = mapped_column(_enum(BatchSourceType, 16), nullable=False)
    provider: Mapped[MusicProviderName] = mapped_column(
        _enum(MusicProviderName, 32), nullable=False
    )
    source_collection_id: Mapped[str] = mapped_column(String(512), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    creator: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[BatchStatus] = mapped_column(
        _enum(BatchStatus, 16), nullable=False, default=BatchStatus.PENDING
    )
    total_items: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("batch_download_requests.id", ondelete="RESTRICT")
    )
    retry_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requested_quality: Mapped[QualityPreference | None] = mapped_column(
        _enum(QualityPreference, 32)
    )
    requested_format: Mapped[FormatPreference | None] = mapped_column(_enum(FormatPreference, 16))
    delivery_mode: Mapped[DeliveryMode | None] = mapped_column(_enum(DeliveryMode, 16))
    embed_metadata: Mapped[bool | None] = mapped_column(Boolean)
    embed_cover: Mapped[bool | None] = mapped_column(Boolean)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    telegram_bot_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    parent_message_id: Mapped[int | None] = mapped_column(BigInteger)


class BatchDownloadItem(TimestampMixin, Base):
    __tablename__ = "batch_download_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "position", name="uq_batch_download_items_position"),
        UniqueConstraint("download_request_id", name="uq_batch_download_items_download_request"),
        CheckConstraint("position >= 1", name="ck_batch_download_items_position"),
        CheckConstraint(
            "status IN ('PENDING','ADMITTED','SKIPPED','FAILED')",
            name="ck_batch_download_items_status",
        ),
        Index("ix_batch_download_items_batch_position", "batch_id", "position"),
        Index("ix_batch_download_items_request", "download_request_id"),
        Index("ix_batch_download_items_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("batch_download_requests.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_media_id: Mapped[str] = mapped_column(String(512), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(1024))
    title: Mapped[str | None] = mapped_column(String(1024))
    artist: Mapped[str | None] = mapped_column(String(1024))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[BatchItemStatus] = mapped_column(
        _enum(BatchItemStatus, 16), nullable=False, default=BatchItemStatus.PENDING
    )
    download_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("download_requests.id", ondelete="SET NULL")
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(256))
