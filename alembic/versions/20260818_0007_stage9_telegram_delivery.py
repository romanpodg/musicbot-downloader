"""Add Stage 9 user preference and durable Telegram delivery outbox.

Revision ID: 20260818_0007
Revises: 20260818_0006
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260818_0007"
down_revision: str | None = "20260818_0006"
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
delivery_status = sa.Enum(
    "AWAITING_QUALITY",
    "QUEUED",
    "WAITING",
    "SENDING",
    "DELIVERED",
    "FAILED",
    "CANCELLED",
    name="telegramdeliverystatus",
    native_enum=False,
    length=24,
    create_constraint=False,
)


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "default_quality",
            new_column_name="preferred_quality_profile",
            existing_type=quality_profile,
            existing_nullable=True,
        )

    op.create_table(
        "telegram_delivery_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_bot_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("quality_profile", quality_profile, nullable=True),
        sa.Column("subscriber_id", sa.String(36), nullable=True),
        sa.Column("download_job_id", sa.Integer(), nullable=True),
        sa.Column("cache_id", sa.Integer(), nullable=True),
        sa.Column("status", delivery_status, nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("repair_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_message_id", sa.BigInteger(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name="ck_telegram_delivery_attempt_count"),
        sa.CheckConstraint(
            "repair_count BETWEEN 0 AND 1", name="ck_telegram_delivery_repair_count"
        ),
        sa.CheckConstraint(
            "quality_profile IS NULL OR quality_profile IN "
            "('MP3_128', 'MP3_320', 'AAC_256', 'LOSSLESS')",
            name="ck_telegram_delivery_quality_profile",
        ),
        sa.CheckConstraint(
            "status IN ('AWAITING_QUALITY', 'QUEUED', 'WAITING', 'SENDING', "
            "'DELIVERED', 'FAILED', 'CANCELLED')",
            name="ck_telegram_delivery_status",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)",
            name="ck_telegram_delivery_lease_pair",
        ),
        sa.ForeignKeyConstraint(["cache_id"], ["telegram_file_cache.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["download_job_id"], ["download_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subscriber_id"], ["job_subscribers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "telegram_bot_id",
            "telegram_chat_id",
            "source_message_id",
            name="uq_telegram_delivery_message",
        ),
    )
    op.create_index(
        "ix_telegram_delivery_claim",
        "telegram_delivery_requests",
        ["status", "available_at", "created_at", "id"],
    )
    op.create_index(
        "ix_telegram_delivery_subscriber", "telegram_delivery_requests", ["subscriber_id"]
    )
    op.create_index("ix_telegram_delivery_user", "telegram_delivery_requests", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_telegram_delivery_user", table_name="telegram_delivery_requests")
    op.drop_index("ix_telegram_delivery_subscriber", table_name="telegram_delivery_requests")
    op.drop_index("ix_telegram_delivery_claim", table_name="telegram_delivery_requests")
    op.drop_table("telegram_delivery_requests")
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "preferred_quality_profile",
            new_column_name="default_quality",
            existing_type=quality_profile,
            existing_nullable=True,
        )
