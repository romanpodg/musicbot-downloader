"""Stage 21 durable intent, lifecycle, and final-delivery records.

The technical ``download_jobs`` table remains owned by the Stage 7 queue.  These
records describe one user's confirmed workflow and therefore cannot be merged
with a shared SingleFlight job.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.delivery_targets import DeliveryTargetType
from app.core.enums import (
    DownloadDeliveryStatus,
    DownloadJobStatus,
    DownloadPhase,
    DownloadSourceType,
    MusicProviderName,
)
from app.storage.models.base import Base, TimestampMixin, UTCDateTime, utc_now


def _enum(
    enum_type: type[DownloadJobStatus]
    | type[DownloadPhase]
    | type[DownloadSourceType]
    | type[DownloadDeliveryStatus]
    | type[DeliveryTargetType],
    length: int,
) -> Enum:
    return Enum(
        enum_type,
        values_callable=lambda values: [member.value for member in values],
        native_enum=False,
        length=length,
        create_constraint=False,
    )


class DownloadRequestRecord(TimestampMixin, Base):
    __tablename__ = "download_requests"
    __table_args__ = (
        UniqueConstraint("confirmation_id", name="uq_download_requests_confirmation"),
        Index("ix_download_requests_user_created", "requester_user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    requester_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    confirmation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[DownloadSourceType] = mapped_column(
        _enum(DownloadSourceType, 32), nullable=False
    )
    source_reference: Mapped[str] = mapped_column(String(512), nullable=False)
    provider: Mapped[MusicProviderName | None] = mapped_column(String(32))
    provider_media_id: Mapped[str | None] = mapped_column(String(512))
    delivery_target_type: Mapped[DeliveryTargetType] = mapped_column(
        _enum(DeliveryTargetType, 16), nullable=False
    )
    delivery_target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)


class DownloadLifecycleJob(TimestampMixin, Base):
    __tablename__ = "download_lifecycle_jobs"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_download_lifecycle_jobs_request"),
        CheckConstraint("attempt >= 0", name="ck_download_lifecycle_jobs_attempt"),
        CheckConstraint("max_attempts >= 1", name="ck_download_lifecycle_jobs_max_attempts"),
        CheckConstraint(
            "status IN ('PENDING','QUEUED','RUNNING','RETRY_WAIT','DELIVERING',"
            "'SUCCEEDED','FAILED','CANCELLED')",
            name="ck_download_lifecycle_jobs_status",
        ),
        CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)",
            name="ck_download_lifecycle_jobs_lease_pair",
        ),
        Index("ix_download_lifecycle_jobs_status", "status"),
        Index("ix_download_lifecycle_jobs_retry", "status", "retry_at"),
        Index("ix_download_lifecycle_jobs_lease", "status", "lease_expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("download_requests.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[DownloadJobStatus] = mapped_column(
        _enum(DownloadJobStatus, 16), nullable=False, default=DownloadJobStatus.PENDING
    )
    phase: Mapped[DownloadPhase | None] = mapped_column(_enum(DownloadPhase, 16))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    queued_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(256))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class DownloadDelivery(TimestampMixin, Base):
    __tablename__ = "download_deliveries"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_download_deliveries_job"),
        CheckConstraint("attempt >= 0", name="ck_download_deliveries_attempt"),
        CheckConstraint(
            "status IN ('PENDING','SENDING','DELIVERED','FAILED')",
            name="ck_download_deliveries_status",
        ),
        Index("ix_download_deliveries_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("download_lifecycle_jobs.id", ondelete="CASCADE"), nullable=False
    )
    telegram_delivery_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("telegram_delivery_requests.id", ondelete="SET NULL"), unique=True
    )
    target_type: Mapped[DeliveryTargetType] = mapped_column(
        _enum(DeliveryTargetType, 16), nullable=False
    )
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[DownloadDeliveryStatus] = mapped_column(
        _enum(DownloadDeliveryStatus, 16), nullable=False, default=DownloadDeliveryStatus.PENDING
    )
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_file_id: Mapped[str | None] = mapped_column(String(512))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
