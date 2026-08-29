"""Add Stage 21 durable download lifecycle records."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260830_0013"
down_revision: str | None = "20260825_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "download_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "requester_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("confirmation_id", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_reference", sa.String(512), nullable=False),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("provider_media_id", sa.String(512), nullable=True),
        sa.Column("delivery_target_type", sa.String(16), nullable=False),
        sa.Column("delivery_target_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("confirmation_id", name="uq_download_requests_confirmation"),
    )
    op.create_index(
        "ix_download_requests_user_created",
        "download_requests",
        ["requester_user_id", "created_at"],
    )
    op.create_table(
        "download_lifecycle_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "request_id",
            sa.Integer(),
            sa.ForeignKey("download_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("phase", sa.String(16), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(256), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("request_id", name="uq_download_lifecycle_jobs_request"),
        sa.CheckConstraint("attempt >= 0", name="ck_download_lifecycle_jobs_attempt"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_download_lifecycle_jobs_max_attempts"),
        sa.CheckConstraint(
            "status IN ('PENDING','QUEUED','RUNNING','RETRY_WAIT','DELIVERING',"
            "'SUCCEEDED','FAILED','CANCELLED')",
            name="ck_download_lifecycle_jobs_status",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)",
            name="ck_download_lifecycle_jobs_lease_pair",
        ),
    )
    op.create_index("ix_download_lifecycle_jobs_status", "download_lifecycle_jobs", ["status"])
    op.create_index(
        "ix_download_lifecycle_jobs_retry", "download_lifecycle_jobs", ["status", "retry_at"]
    )
    op.create_index(
        "ix_download_lifecycle_jobs_lease",
        "download_lifecycle_jobs",
        ["status", "lease_expires_at"],
    )
    op.create_table(
        "download_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("download_lifecycle_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "telegram_delivery_request_id",
            sa.Integer(),
            sa.ForeignKey("telegram_delivery_requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_file_id", sa.String(512), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", name="uq_download_deliveries_job"),
        sa.UniqueConstraint(
            "telegram_delivery_request_id", name="uq_download_deliveries_telegram_request"
        ),
        sa.CheckConstraint("attempt >= 0", name="ck_download_deliveries_attempt"),
        sa.CheckConstraint(
            "status IN ('PENDING','SENDING','DELIVERED','FAILED')",
            name="ck_download_deliveries_status",
        ),
    )
    op.create_index("ix_download_deliveries_status", "download_deliveries", ["status"])


def downgrade() -> None:
    op.drop_index("ix_download_deliveries_status", table_name="download_deliveries")
    op.drop_table("download_deliveries")
    op.drop_index("ix_download_lifecycle_jobs_lease", table_name="download_lifecycle_jobs")
    op.drop_index("ix_download_lifecycle_jobs_retry", table_name="download_lifecycle_jobs")
    op.drop_index("ix_download_lifecycle_jobs_status", table_name="download_lifecycle_jobs")
    op.drop_table("download_lifecycle_jobs")
    op.drop_index("ix_download_requests_user_created", table_name="download_requests")
    op.drop_table("download_requests")
