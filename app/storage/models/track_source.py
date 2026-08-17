"""Provider identity attached to a canonical track."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import MusicProviderName
from app.storage.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.storage.models.track import Track


class TrackSource(TimestampMixin, Base):
    __tablename__ = "track_sources"
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_track_id", name="uq_track_sources_provider_track_id"
        ),
        Index("ix_track_sources_track_id", "track_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    track_id: Mapped[int] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[MusicProviderName] = mapped_column(
        Enum(MusicProviderName, native_enum=False, length=32), nullable=False
    )
    provider_track_id: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[str | None] = mapped_column(String(2048))
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata_json", JSON, default=dict, nullable=False
    )

    track: Mapped[Track] = relationship(back_populates="sources")
