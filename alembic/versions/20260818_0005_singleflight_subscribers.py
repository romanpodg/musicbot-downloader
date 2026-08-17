"""Add Stage 7.1 SingleFlight coordination and job subscribers.

Revision ID: 20260818_0005
Revises: 20260818_0004
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260818_0005"
down_revision: str | None = "20260818_0004"
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
subscriber_status = sa.Enum(
    "WAITING",
    "READY",
    "FAILED",
    "CANCELLED",
    name="subscriberstatus",
    native_enum=False,
    length=16,
    create_constraint=False,
)


def upgrade() -> None:
    op.create_table(
        "download_flights",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("quality_profile", quality_profile, nullable=False),
        sa.Column("download_job_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "quality_profile IN ('MP3_128', 'MP3_320', 'AAC_256', 'LOSSLESS')",
            name="ck_download_flights_quality_profile",
        ),
        sa.ForeignKeyConstraint(["download_job_id"], ["download_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("download_job_id", name="uq_download_flights_download_job_id"),
        sa.UniqueConstraint("track_id", "quality_profile", name="uq_download_flights_key"),
    )
    op.create_table(
        "job_subscribers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("download_job_id", sa.Integer(), nullable=False),
        sa.Column("status", subscriber_status, nullable=False),
        sa.Column("request_key", sa.String(length=128), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('WAITING', 'READY', 'FAILED', 'CANCELLED')",
            name="ck_job_subscribers_status",
        ),
        sa.ForeignKeyConstraint(["download_job_id"], ["download_jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "download_job_id", "request_key", name="uq_job_subscribers_request_key"
        ),
    )
    op.create_index(
        "ix_job_subscribers_job_status",
        "job_subscribers",
        ["download_job_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_job_subscribers_status_created",
        "job_subscribers",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_job_subscribers_status_created", table_name="job_subscribers")
    op.drop_index("ix_job_subscribers_job_status", table_name="job_subscribers")
    op.drop_table("job_subscribers")
    op.drop_table("download_flights")
