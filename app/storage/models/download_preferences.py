"""Durable Stage 22 per-user download preferences."""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Enum, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import DeliveryMode, FormatPreference, QualityPreference
from app.storage.models.base import Base, TimestampMixin


class UserDownloadPreferencesRecord(TimestampMixin, Base):
    __tablename__ = "user_download_preferences"
    __table_args__ = (
        CheckConstraint(
            "quality IN ('best_available','lossless','high','standard')",
            name="ck_user_download_preferences_quality",
        ),
        CheckConstraint(
            "format IN ('original','flac','mp3','m4a')",
            name="ck_user_download_preferences_format",
        ),
        CheckConstraint(
            "delivery_mode IN ('audio','document')",
            name="ck_user_download_preferences_delivery_mode",
        ),
        CheckConstraint(
            "NOT (quality = 'lossless' AND format IN ('mp3','m4a'))",
            name="ck_user_download_preferences_quality_format",
        ),
        Index("ix_user_download_preferences_updated", "updated_at"),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    quality: Mapped[QualityPreference] = mapped_column(
        Enum(
            QualityPreference,
            values_callable=lambda values: [item.value for item in values],
            native_enum=False,
            length=32,
        ),
        nullable=False,
    )
    format: Mapped[FormatPreference] = mapped_column(
        Enum(
            FormatPreference,
            values_callable=lambda values: [item.value for item in values],
            native_enum=False,
            length=16,
        ),
        nullable=False,
    )
    delivery_mode: Mapped[DeliveryMode] = mapped_column(
        Enum(
            DeliveryMode,
            values_callable=lambda values: [item.value for item in values],
            native_enum=False,
            length=16,
        ),
        nullable=False,
    )
    embed_metadata: Mapped[bool] = mapped_column(Boolean, nullable=False)
    embed_cover: Mapped[bool] = mapped_column(Boolean, nullable=False)
