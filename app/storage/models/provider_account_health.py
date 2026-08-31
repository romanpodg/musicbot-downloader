"""Application-owned, non-secret provider account health projection."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.models.base import Base, TimestampMixin, UTCDateTime


class ProviderAccountHealthRecord(TimestampMixin, Base):
    """Durable health only; credentials and runtime account payloads stay child-owned."""

    __tablename__ = "account_health"
    __table_args__ = (
        UniqueConstraint("provider", "account_id", name="uq_provider_account_health_account"),
        Index("ix_account_health_provider_state", "provider", "health_state"),
        Index("ix_account_health_cooldown", "cooldown_until"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    health_state: Mapped[str] = mapped_column(String(32), nullable=False, default="HEALTHY")
    failure_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_failure_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cooldown_until: Mapped[datetime | None] = mapped_column(UTCDateTime())


__all__ = ["ProviderAccountHealthRecord"]
