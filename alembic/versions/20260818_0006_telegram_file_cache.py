"""Add Stage 8 Telegram completed-result cache and durable upload provenance.

Revision ID: 20260818_0006
Revises: 20260818_0005
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260818_0006"
down_revision: str | None = "20260818_0005"
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
provider_name = sa.Enum(
    "apple_music",
    "bandcamp",
    "deezer",
    "qobuz",
    "soundcloud",
    "spotify",
    "tidal",
    "youtube_music",
    name="musicprovidername",
    native_enum=False,
    length=32,
    create_constraint=False,
)
operation = sa.Enum(
    "DIRECT",
    "TRANSCODE",
    name="downloadplanoperation",
    native_enum=False,
    length=16,
    create_constraint=False,
)
codec = sa.Enum(
    "mp3",
    "aac",
    "flac",
    "vorbis",
    "opus",
    "unknown",
    "other",
    name="nativecodec",
    native_enum=False,
    length=16,
    create_constraint=False,
)
container = sa.Enum(
    "mp3",
    "m4a",
    "flac",
    "ogg",
    "webm",
    "unknown",
    "other",
    name="nativecontainer",
    native_enum=False,
    length=16,
    create_constraint=False,
)
media_kind = sa.Enum(
    "AUDIO",
    "DOCUMENT",
    name="telegrammediakind",
    native_enum=False,
    length=16,
    create_constraint=False,
)
cache_status = sa.Enum(
    "ACTIVE",
    "INVALID",
    name="telegramcachestatus",
    native_enum=False,
    length=16,
    create_constraint=False,
)


def upgrade() -> None:
    with op.batch_alter_table("upload_jobs") as batch_op:
        batch_op.add_column(sa.Column("source_track_source_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("source_provider", provider_name, nullable=True))
        batch_op.add_column(sa.Column("source_provider_track_id", sa.String(512), nullable=True))
        batch_op.add_column(sa.Column("operation", operation, nullable=True))
        batch_op.add_column(sa.Column("transcoded", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("source_codec", codec, nullable=True))
        batch_op.add_column(sa.Column("source_container", container, nullable=True))
        batch_op.add_column(sa.Column("source_bitrate_kbps", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("output_codec", codec, nullable=True))
        batch_op.add_column(sa.Column("output_container", container, nullable=True))
        batch_op.add_column(sa.Column("output_bitrate_kbps", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("sample_rate_hz", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("bit_depth", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("channels", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("duration_ms", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("file_size_bytes", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("encoder", sa.String(64), nullable=True))
        batch_op.create_foreign_key(
            "fk_upload_jobs_source_track_source_id",
            "track_sources",
            ["source_track_source_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "telegram_file_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_bot_id", sa.BigInteger(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("quality_profile", quality_profile, nullable=False),
        sa.Column("telegram_file_id", sa.String(512), nullable=False),
        sa.Column("telegram_file_unique_id", sa.String(512), nullable=False),
        sa.Column("telegram_media_kind", media_kind, nullable=False),
        sa.Column("cache_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("cache_message_id", sa.BigInteger(), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("source_track_source_id", sa.Integer(), nullable=True),
        sa.Column("source_provider", provider_name, nullable=False),
        sa.Column("source_provider_track_id", sa.String(512), nullable=False),
        sa.Column("operation", operation, nullable=False),
        sa.Column("transcoded", sa.Boolean(), nullable=False),
        sa.Column("source_codec", codec, nullable=True),
        sa.Column("source_container", container, nullable=True),
        sa.Column("source_bitrate_kbps", sa.Integer(), nullable=True),
        sa.Column("output_codec", codec, nullable=True),
        sa.Column("output_container", container, nullable=True),
        sa.Column("output_bitrate_kbps", sa.Integer(), nullable=True),
        sa.Column("sample_rate_hz", sa.Integer(), nullable=True),
        sa.Column("bit_depth", sa.Integer(), nullable=True),
        sa.Column("channels", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("encoder", sa.String(64), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", cache_status, nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalid_reason_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "quality_profile IN ('MP3_128', 'MP3_320', 'AAC_256', 'LOSSLESS')",
            name="ck_telegram_file_cache_quality_profile",
        ),
        sa.CheckConstraint(
            "telegram_media_kind IN ('AUDIO', 'DOCUMENT')",
            name="ck_telegram_file_cache_media_kind",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'INVALID')",
            name="ck_telegram_file_cache_status",
        ),
        sa.CheckConstraint("file_size_bytes > 0", name="ck_telegram_file_cache_file_size"),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND invalidated_at IS NULL) OR "
            "(status = 'INVALID' AND invalidated_at IS NOT NULL)",
            name="ck_telegram_file_cache_invalidation",
        ),
        sa.ForeignKeyConstraint(
            ["source_track_source_id"], ["track_sources.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "telegram_bot_id",
            "track_id",
            "quality_profile",
            name="uq_telegram_file_cache_key",
        ),
    )
    op.create_index(
        "ix_telegram_file_cache_status_created",
        "telegram_file_cache",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_telegram_file_cache_status_created", table_name="telegram_file_cache")
    op.drop_table("telegram_file_cache")
    with op.batch_alter_table("upload_jobs") as batch_op:
        batch_op.drop_constraint("fk_upload_jobs_source_track_source_id", type_="foreignkey")
        for column in (
            "encoder",
            "file_size_bytes",
            "duration_ms",
            "channels",
            "bit_depth",
            "sample_rate_hz",
            "output_bitrate_kbps",
            "output_container",
            "output_codec",
            "source_bitrate_kbps",
            "source_container",
            "source_codec",
            "transcoded",
            "operation",
            "source_provider_track_id",
            "source_provider",
            "source_track_source_id",
        ):
            batch_op.drop_column(column)
