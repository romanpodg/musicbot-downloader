"""Add Stage 9.3 durable album snapshots and child delivery origins.

Revision ID: 20260818_0009
Revises: 20260818_0008
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260818_0009"
down_revision: str | None = "20260818_0008"
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
provider = sa.Enum(
    "apple_music",
    "bandcamp",
    "deezer",
    "qobuz",
    "soundcloud",
    "spotify",
    "tidal",
    "youtube_music",
    name="musicprovider",
    native_enum=False,
    length=32,
    create_constraint=False,
)
album_status = sa.Enum(
    "AWAITING_QUALITY",
    "AWAITING_ACTION",
    "AWAITING_ALBUM_QUALITY",
    "SELECTING_TRACKS",
    "QUEUED",
    "PROCESSING",
    "COMPLETED",
    "PARTIALLY_FAILED",
    "FAILED",
    "CANCELLED",
    name="telegramalbumrequeststatus",
    native_enum=False,
    length=32,
    create_constraint=False,
)
item_status = sa.Enum(
    "PENDING",
    "RESOLVING",
    "ATTACHED",
    "FAILED",
    "CANCELLED",
    name="telegramalbumitemstatus",
    native_enum=False,
    length=16,
    create_constraint=False,
)


def upgrade() -> None:
    op.create_table(
        "telegram_album_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_bot_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", provider, nullable=False),
        sa.Column("provider_album_id", sa.String(2048), nullable=False),
        sa.Column("title", sa.String(1024), nullable=False),
        sa.Column("artist", sa.String(1024), nullable=False),
        sa.Column("release_date", sa.String(32), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("track_count", sa.Integer(), nullable=False),
        sa.Column("quality_profile", quality_profile, nullable=True),
        sa.Column("status", album_status, nullable=False),
        sa.Column("card_message_id", sa.BigInteger(), nullable=True),
        sa.Column("completion_message_id", sa.BigInteger(), nullable=True),
        sa.Column("completion_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("track_count BETWEEN 1 AND 500", name="ck_telegram_album_track_count"),
        sa.CheckConstraint(
            "quality_profile IS NULL OR quality_profile IN "
            "('MP3_128', 'MP3_320', 'AAC_256', 'LOSSLESS')",
            name="ck_telegram_album_quality_profile",
        ),
        sa.CheckConstraint(
            "status IN ('AWAITING_QUALITY', 'AWAITING_ACTION', 'AWAITING_ALBUM_QUALITY', "
            "'SELECTING_TRACKS', 'QUEUED', 'PROCESSING', 'COMPLETED', "
            "'PARTIALLY_FAILED', 'FAILED', 'CANCELLED')",
            name="ck_telegram_album_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "telegram_bot_id",
            "telegram_chat_id",
            "source_message_id",
            name="uq_telegram_album_message",
        ),
    )
    op.create_index(
        "ix_telegram_album_claim",
        "telegram_album_requests",
        ["status", "updated_at", "id"],
    )
    op.create_index("ix_telegram_album_user", "telegram_album_requests", ["user_id"])

    op.create_table(
        "telegram_album_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("album_request_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("disc_number", sa.Integer(), nullable=True),
        sa.Column("track_number", sa.Integer(), nullable=True),
        sa.Column("provider_track_id", sa.String(2048), nullable=False),
        sa.Column("title", sa.String(1024), nullable=True),
        sa.Column("artist", sa.String(1024), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("explicit", sa.Boolean(), nullable=True),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("resolution_status", item_status, nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("position >= 1", name="ck_telegram_album_item_position"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_telegram_album_item_attempt_count"),
        sa.CheckConstraint(
            "resolution_status IN ('PENDING', 'RESOLVING', 'ATTACHED', 'FAILED', 'CANCELLED')",
            name="ck_telegram_album_item_resolution_status",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)",
            name="ck_telegram_album_item_lease_pair",
        ),
        sa.ForeignKeyConstraint(
            ["album_request_id"], ["telegram_album_requests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("album_request_id", "position", name="uq_telegram_album_item_position"),
    )
    op.create_index(
        "ix_telegram_album_item_claim",
        "telegram_album_items",
        [
            "album_request_id",
            "selected",
            "resolution_status",
            "available_at",
            "position",
        ],
    )
    op.create_index("ix_telegram_album_item_track", "telegram_album_items", ["track_id"])

    with op.batch_alter_table("telegram_delivery_requests") as batch_op:
        batch_op.alter_column("source_message_id", existing_type=sa.BigInteger(), nullable=True)
        batch_op.add_column(sa.Column("album_item_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_telegram_delivery_album_item",
            "telegram_album_items",
            ["album_item_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_unique_constraint("uq_telegram_delivery_album_item", ["album_item_id"])
        batch_op.create_check_constraint(
            "ck_telegram_delivery_origin",
            "(source_message_id IS NOT NULL AND album_item_id IS NULL) OR "
            "(source_message_id IS NULL AND album_item_id IS NOT NULL)",
        )


def downgrade() -> None:
    op.execute("DELETE FROM telegram_delivery_requests WHERE album_item_id IS NOT NULL")
    with op.batch_alter_table("telegram_delivery_requests") as batch_op:
        batch_op.drop_constraint("ck_telegram_delivery_origin", type_="check")
        batch_op.drop_constraint("uq_telegram_delivery_album_item", type_="unique")
        batch_op.drop_constraint("fk_telegram_delivery_album_item", type_="foreignkey")
        batch_op.drop_column("album_item_id")
        batch_op.alter_column("source_message_id", existing_type=sa.BigInteger(), nullable=False)
    op.drop_index("ix_telegram_album_item_track", table_name="telegram_album_items")
    op.drop_index("ix_telegram_album_item_claim", table_name="telegram_album_items")
    op.drop_table("telegram_album_items")
    op.drop_index("ix_telegram_album_user", table_name="telegram_album_requests")
    op.drop_index("ix_telegram_album_claim", table_name="telegram_album_requests")
    op.drop_table("telegram_album_requests")
