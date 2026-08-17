"""Canonical, provider-independent track."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.storage.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.storage.models.track_source import TrackSource


class Track(TimestampMixin, Base):
    __tablename__ = "tracks"
    __table_args__ = (
        Index("ix_tracks_isrc", "isrc"),
        Index("ix_tracks_normalized_artist_title", "normalized_artist", "normalized_title"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    isrc: Mapped[str | None] = mapped_column(String(12))
    title: Mapped[str | None] = mapped_column(String(512))
    artist: Mapped[str | None] = mapped_column(String(512))
    normalized_title: Mapped[str | None] = mapped_column(String(512))
    normalized_artist: Mapped[str | None] = mapped_column(String(512))
    album: Mapped[str | None] = mapped_column(String(512))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    release_date: Mapped[date | None] = mapped_column(Date)
    explicit: Mapped[bool | None] = mapped_column(Boolean)

    sources: Mapped[list[TrackSource]] = relationship(
        back_populates="track", cascade="all, delete-orphan", passive_deletes=True
    )
