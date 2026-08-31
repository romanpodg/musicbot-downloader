"""Durable Stage 25 candidate snapshots and provider-attempt audit."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import MusicProviderName
from app.storage.models.base import Base, TimestampMixin, UTCDateTime


class DownloadProviderCandidateRecord(TimestampMixin, Base):
    __tablename__ = "download_provider_candidates"
    __table_args__ = (
        UniqueConstraint(
            "request_id", "provider", "provider_media_id", name="uq_download_provider_candidate"
        ),
        Index("ix_download_provider_candidates_request", "request_id"),
        Index("ix_download_provider_candidates_provider_media", "provider", "provider_media_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("download_requests.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[MusicProviderName] = mapped_column(String(32), nullable=False)
    provider_media_id: Mapped[str] = mapped_column(String(512), nullable=False)
    match_score: Mapped[float] = mapped_column(nullable=False)
    match_method: Mapped[str] = mapped_column(String(32), nullable=False)
    match_reasons: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    media_capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(2048))
    identity_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class DownloadProviderAttemptRecord(TimestampMixin, Base):
    __tablename__ = "download_provider_attempts"
    __table_args__ = (
        Index("ix_download_provider_attempts_job_number", "job_id", "attempt_number"),
        Index("ix_download_provider_attempts_candidate", "candidate_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("download_lifecycle_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("download_provider_candidates.id", ondelete="RESTRICT"), nullable=False
    )
    provider_account_id: Mapped[str | None] = mapped_column(String(128))
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    fallback_decision: Mapped[str | None] = mapped_column(String(64))


# Stable semantic aliases used by Stage 25 callers.
ProviderCandidateRecord = DownloadProviderCandidateRecord
ProviderAttempt = DownloadProviderAttemptRecord
