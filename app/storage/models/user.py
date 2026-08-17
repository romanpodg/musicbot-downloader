"""Persisted application user."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import QualityProfile, UserRole
from app.storage.models.base import Base, TimestampMixin, UTCDateTime, utc_now


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False, length=16), default=UserRole.USER, nullable=False
    )
    telegram_language_code: Mapped[str | None] = mapped_column(String(32))
    preferred_locale: Mapped[str | None] = mapped_column(String(32))
    preferred_quality_profile: Mapped[QualityProfile | None] = mapped_column(
        Enum(QualityProfile, native_enum=False, length=16)
    )
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    @property
    def default_quality(self) -> QualityProfile | None:
        """Compatibility alias for the pre-Stage-9 internal field name."""

        return self.preferred_quality_profile
