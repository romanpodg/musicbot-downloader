"""Initial users, tracks, and track_sources schema.

Revision ID: 20260817_0001
Revises:
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260817_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tracks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("isrc", sa.String(length=12), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("artist", sa.String(length=512), nullable=True),
        sa.Column("album", sa.String(length=512), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("explicit", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tracks_isrc", "tracks", ["isrc"], unique=False)

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=255), nullable=True),
        sa.Column(
            "role",
            sa.Enum("USER", "ADMIN", "OWNER", name="userrole", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column("telegram_language_code", sa.String(length=32), nullable=True),
        sa.Column("preferred_locale", sa.String(length=32), nullable=True),
        sa.Column(
            "default_quality",
            sa.Enum(
                "MP3_128",
                "MP3_320",
                "AAC_256",
                "LOSSLESS",
                name="qualityprofile",
                native_enum=False,
                length=16,
            ),
            nullable=True,
        ),
        sa.Column("is_banned", sa.Boolean(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"], unique=True)

    op.create_table(
        "track_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column(
            "provider",
            sa.Enum(
                "APPLE_MUSIC",
                "BANDCAMP",
                "DEEZER",
                "QOBUZ",
                "SOUNDCLOUD",
                "SPOTIFY",
                "TIDAL",
                "YOUTUBE_MUSIC",
                name="musicprovidername",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("provider_track_id", sa.String(length=512), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "provider_track_id", name="uq_track_sources_provider_track_id"
        ),
    )
    op.create_index("ix_track_sources_track_id", "track_sources", ["track_id"])


def downgrade() -> None:
    op.drop_index("ix_track_sources_track_id", table_name="track_sources")
    op.drop_table("track_sources")
    op.drop_index("ix_users_telegram_id", table_name="users")
    op.drop_table("users")
    op.drop_index("ix_tracks_isrc", table_name="tracks")
    op.drop_table("tracks")
