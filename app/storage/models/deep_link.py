"""Bot-scoped durable targets for public Telegram start parameters."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import DeepLinkStatus, DeepLinkTargetType, MusicProviderName
from app.storage.models.base import Base, TimestampMixin, UTCDateTime


def _enum(enum_type: type, name: str, length: int) -> Enum:
    return Enum(
        enum_type,
        values_callable=lambda values: [member.value for member in values],
        name=name,
        native_enum=False,
        length=length,
        create_constraint=False,
    )


class DeepLinkRegistryEntry(TimestampMixin, Base):
    __tablename__ = "deep_link_registry"
    __table_args__ = (
        CheckConstraint("telegram_bot_id > 0", name="ck_deep_link_bot_id"),
        CheckConstraint("target_type IN ('TRACK', 'ALBUM')", name="ck_deep_link_target_type"),
        CheckConstraint("status IN ('ACTIVE', 'REVOKED')", name="ck_deep_link_status"),
        CheckConstraint(
            "album_provider IS NULL OR album_provider IN ('apple_music', 'bandcamp', "
            "'deezer', 'qobuz', 'soundcloud', 'spotify', 'tidal', 'youtube_music')",
            name="ck_deep_link_album_provider",
        ),
        CheckConstraint(
            "(target_type = 'TRACK' AND track_id IS NOT NULL AND album_provider IS NULL "
            "AND album_provider_id IS NULL) OR (target_type = 'ALBUM' AND track_id IS NULL "
            "AND album_provider IS NOT NULL AND album_provider_id IS NOT NULL)",
            name="ck_deep_link_target_shape",
        ),
        CheckConstraint(
            "album_provider_id IS NULL OR length(album_provider_id) BETWEEN 1 AND 2048",
            name="ck_deep_link_album_provider_id",
        ),
        CheckConstraint(
            "idempotency_key IS NULL OR length(idempotency_key) BETWEEN 1 AND 128",
            name="ck_deep_link_idempotency_key",
        ),
        CheckConstraint("length(request_fingerprint) = 64", name="ck_deep_link_fingerprint"),
        CheckConstraint("length(token) = 35", name="ck_deep_link_token_length"),
        CheckConstraint(
            "substr(token, 1, 3) = 'd1_' AND token NOT GLOB '*[^A-Za-z0-9_-]*'",
            name="ck_deep_link_token_format",
        ),
        CheckConstraint(
            "(status = 'ACTIVE' AND revoked_at IS NULL) OR "
            "(status = 'REVOKED' AND revoked_at IS NOT NULL)",
            name="ck_deep_link_revocation_state",
        ),
        UniqueConstraint("telegram_bot_id", "token", name="uq_deep_link_bot_token"),
        UniqueConstraint("telegram_bot_id", "idempotency_key", name="uq_deep_link_bot_idempotency"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_bot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    token: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[DeepLinkTargetType] = mapped_column(
        _enum(DeepLinkTargetType, "deeplinktargettype", 8), nullable=False
    )
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id", ondelete="RESTRICT"))
    album_provider: Mapped[MusicProviderName | None] = mapped_column(
        _enum(MusicProviderName, "musicprovider", 32)
    )
    album_provider_id: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[DeepLinkStatus] = mapped_column(
        _enum(DeepLinkStatus, "deeplinkstatus", 8), nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
