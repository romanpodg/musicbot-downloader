"""Add persistent Stage 7 queues and runtime worker settings.

Revision ID: 20260818_0004
Revises: 20260817_0003
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260818_0004"
down_revision: str | None = "20260817_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

quality_profile = sa.Enum(
    "MP3_128",
    "MP3_320",
    "AAC_256",
    "LOSSLESS",
    name="qualityprofile",
    native_enum=False,
    length=16,
    create_constraint=False,
)
queue_status = sa.Enum(
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    name="queuejobstatus",
    native_enum=False,
    length=16,
    create_constraint=False,
)


def upgrade() -> None:
    op.create_table(
        "download_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("quality_profile", quality_profile, nullable=False),
        sa.Column("status", queue_status, nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_detail", sa.String(length=256), nullable=True),
        sa.Column("artifact_job_id", sa.String(length=32), nullable=True),
        sa.Column("artifact_path", sa.String(length=2048), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name="ck_download_jobs_attempt_count"),
        sa.CheckConstraint(
            "quality_profile IN ('MP3_128', 'MP3_320', 'AAC_256', 'LOSSLESS')",
            name="ck_download_jobs_quality_profile",
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')",
            name="ck_download_jobs_status",
        ),
        sa.CheckConstraint(
            "(artifact_job_id IS NULL) = (artifact_path IS NULL)",
            name="ck_download_jobs_artifact_pair",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)",
            name="ck_download_jobs_lease_pair",
        ),
        sa.CheckConstraint(
            "status != 'RUNNING' OR lease_owner IS NOT NULL",
            name="ck_download_jobs_running_lease",
        ),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_download_jobs_claim",
        "download_jobs",
        ["status", "available_at", "queued_at", "id"],
        unique=False,
    )

    op.create_table(
        "upload_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("download_job_id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("quality_profile", quality_profile, nullable=False),
        sa.Column("status", queue_status, nullable=False),
        sa.Column("artifact_job_id", sa.String(length=32), nullable=False),
        sa.Column("artifact_path", sa.String(length=2048), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_detail", sa.String(length=256), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name="ck_upload_jobs_attempt_count"),
        sa.CheckConstraint(
            "quality_profile IN ('MP3_128', 'MP3_320', 'AAC_256', 'LOSSLESS')",
            name="ck_upload_jobs_quality_profile",
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')",
            name="ck_upload_jobs_status",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)",
            name="ck_upload_jobs_lease_pair",
        ),
        sa.CheckConstraint(
            "status != 'RUNNING' OR lease_owner IS NOT NULL",
            name="ck_upload_jobs_running_lease",
        ),
        sa.ForeignKeyConstraint(["download_job_id"], ["download_jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("download_job_id"),
    )
    op.create_index(
        "ix_upload_jobs_claim",
        "upload_jobs",
        ["status", "available_at", "queued_at", "id"],
        unique=False,
    )

    op.create_table(
        "runtime_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("download_workers", sa.Integer(), nullable=False),
        sa.Column("upload_workers", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_runtime_settings_singleton"),
        sa.CheckConstraint("download_workers >= 1", name="ck_runtime_download_workers_positive"),
        sa.CheckConstraint("upload_workers >= 1", name="ck_runtime_upload_workers_positive"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("runtime_settings")
    op.drop_index("ix_upload_jobs_claim", table_name="upload_jobs")
    op.drop_table("upload_jobs")
    op.drop_index("ix_download_jobs_claim", table_name="download_jobs")
    op.drop_table("download_jobs")
