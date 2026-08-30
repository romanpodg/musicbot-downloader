"""Stage 24 artifact-exact Telegram file handles.

This is deliberately separate from Stage 8's technical shared-upload cache: a
row here is keyed by the final immutable Stage 22 profile snapshot and is only
consulted from a durable Stage 21 delivery.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.models.base import Base, TimestampMixin, UTCDateTime


class TelegramArtifactCacheEntry(TimestampMixin, Base):
    __tablename__ = "telegram_artifact_cache_entries"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_telegram_artifact_cache_fingerprint"),
        Index("ix_telegram_artifact_cache_active_used", "invalidated_at", "last_used_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_media_id: Mapped[str] = mapped_column(String(512), nullable=False)
    effective_quality: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_format: Mapped[str] = mapped_column(String(16), nullable=False)
    delivery_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    embed_metadata: Mapped[bool] = mapped_column(Boolean, nullable=False)
    embed_cover: Mapped[bool] = mapped_column(Boolean, nullable=False)
    artifact_processing_version: Mapped[int] = mapped_column(Integer, nullable=False)
    telegram_file_id: Mapped[str] = mapped_column(String(512), nullable=False)
    telegram_file_unique_id: Mapped[str | None] = mapped_column(String(512))
    source_delivery_id: Mapped[int | None] = mapped_column(
        ForeignKey("download_deliveries.id", ondelete="SET NULL")
    )
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    invalidated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    invalidation_reason: Mapped[str | None] = mapped_column(String(64))
