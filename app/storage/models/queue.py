"""Persistent Stage 7 queue and runtime-setting models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Enum, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import QualityProfile, QueueJobStatus
from app.storage.models.base import Base, TimestampMixin, UTCDateTime, utc_now


def _enum(enum_type: type[QueueJobStatus] | type[QualityProfile], name: str, length: int) -> Enum:
    return Enum(
        enum_type,
        values_callable=lambda values: [member.value for member in values],
        name=name,
        native_enum=False,
        length=length,
        create_constraint=False,
    )


class DownloadJob(TimestampMixin, Base):
    __tablename__ = "download_jobs"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_download_jobs_attempt_count"),
        CheckConstraint(
            "quality_profile IN ('MP3_128', 'MP3_320', 'AAC_256', 'LOSSLESS')",
            name="ck_download_jobs_quality_profile",
        ),
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')",
            name="ck_download_jobs_status",
        ),
        CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)",
            name="ck_download_jobs_lease_pair",
        ),
        CheckConstraint(
            "status != 'RUNNING' OR lease_owner IS NOT NULL",
            name="ck_download_jobs_running_lease",
        ),
        CheckConstraint(
            "(artifact_job_id IS NULL) = (artifact_path IS NULL)",
            name="ck_download_jobs_artifact_pair",
        ),
        Index(
            "ix_download_jobs_claim",
            "status",
            "available_at",
            "queued_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    track_id: Mapped[int] = mapped_column(
        ForeignKey("tracks.id", ondelete="RESTRICT"), nullable=False
    )
    quality_profile: Mapped[QualityProfile] = mapped_column(
        _enum(QualityProfile, "qualityprofile", 16), nullable=False
    )
    status: Mapped[QueueJobStatus] = mapped_column(
        _enum(QueueJobStatus, "queuejobstatus", 16), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    queued_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_detail: Mapped[str | None] = mapped_column(String(256))
    artifact_job_id: Mapped[str | None] = mapped_column(String(32))
    artifact_path: Mapped[str | None] = mapped_column(String(2048))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class UploadJob(TimestampMixin, Base):
    __tablename__ = "upload_jobs"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_upload_jobs_attempt_count"),
        CheckConstraint(
            "quality_profile IN ('MP3_128', 'MP3_320', 'AAC_256', 'LOSSLESS')",
            name="ck_upload_jobs_quality_profile",
        ),
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')",
            name="ck_upload_jobs_status",
        ),
        CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)",
            name="ck_upload_jobs_lease_pair",
        ),
        CheckConstraint(
            "status != 'RUNNING' OR lease_owner IS NOT NULL",
            name="ck_upload_jobs_running_lease",
        ),
        Index(
            "ix_upload_jobs_claim",
            "status",
            "available_at",
            "queued_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    download_job_id: Mapped[int] = mapped_column(
        ForeignKey("download_jobs.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    track_id: Mapped[int] = mapped_column(
        ForeignKey("tracks.id", ondelete="RESTRICT"), nullable=False
    )
    quality_profile: Mapped[QualityProfile] = mapped_column(
        _enum(QualityProfile, "qualityprofile", 16), nullable=False
    )
    status: Mapped[QueueJobStatus] = mapped_column(
        _enum(QueueJobStatus, "queuejobstatus", 16), nullable=False
    )
    artifact_job_id: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    queued_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_detail: Mapped[str | None] = mapped_column(String(256))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class RuntimeSettings(TimestampMixin, Base):
    __tablename__ = "runtime_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_runtime_settings_singleton"),
        CheckConstraint("download_workers >= 1", name="ck_runtime_download_workers_positive"),
        CheckConstraint("upload_workers >= 1", name="ck_runtime_upload_workers_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    download_workers: Mapped[int] = mapped_column(Integer, nullable=False)
    upload_workers: Mapped[int] = mapped_column(Integer, nullable=False)
