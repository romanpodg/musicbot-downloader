"""Add the Stage 11 bot-scoped opaque deep-link registry.

Revision ID: 20260820_0010
Revises: 20260818_0009
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260820_0010"
down_revision: str | None = "20260818_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

target_type = sa.Enum(
    "TRACK",
    "ALBUM",
    name="deeplinktargettype",
    native_enum=False,
    length=8,
    create_constraint=False,
)
status = sa.Enum(
    "ACTIVE", "REVOKED", name="deeplinkstatus", native_enum=False, length=8, create_constraint=False
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


def upgrade() -> None:
    op.create_table(
        "deep_link_registry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_bot_id", sa.BigInteger(), nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("target_type", target_type, nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=True),
        sa.Column("album_provider", provider, nullable=True),
        sa.Column("album_provider_id", sa.String(2048), nullable=True),
        sa.Column("status", status, nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("telegram_bot_id > 0", name="ck_deep_link_bot_id"),
        sa.CheckConstraint("target_type IN ('TRACK', 'ALBUM')", name="ck_deep_link_target_type"),
        sa.CheckConstraint("status IN ('ACTIVE', 'REVOKED')", name="ck_deep_link_status"),
        sa.CheckConstraint(
            "album_provider IS NULL OR album_provider IN ('apple_music', 'bandcamp', "
            "'deezer', 'qobuz', 'soundcloud', 'spotify', 'tidal', 'youtube_music')",
            name="ck_deep_link_album_provider",
        ),
        sa.CheckConstraint(
            "(target_type = 'TRACK' AND track_id IS NOT NULL AND album_provider IS NULL "
            "AND album_provider_id IS NULL) OR (target_type = 'ALBUM' AND track_id IS NULL "
            "AND album_provider IS NOT NULL AND album_provider_id IS NOT NULL)",
            name="ck_deep_link_target_shape",
        ),
        sa.CheckConstraint(
            "album_provider_id IS NULL OR length(album_provider_id) BETWEEN 1 AND 2048",
            name="ck_deep_link_album_provider_id",
        ),
        sa.CheckConstraint(
            "idempotency_key IS NULL OR length(idempotency_key) BETWEEN 1 AND 128",
            name="ck_deep_link_idempotency_key",
        ),
        sa.CheckConstraint("length(request_fingerprint) = 64", name="ck_deep_link_fingerprint"),
        sa.CheckConstraint("length(token) = 35", name="ck_deep_link_token_length"),
        sa.CheckConstraint(
            "substr(token, 1, 3) = 'd1_' AND token NOT GLOB '*[^A-Za-z0-9_-]*'",
            name="ck_deep_link_token_format",
        ),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND revoked_at IS NULL) OR "
            "(status = 'REVOKED' AND revoked_at IS NOT NULL)",
            name="ck_deep_link_revocation_state",
        ),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_bot_id", "token", name="uq_deep_link_bot_token"),
        sa.UniqueConstraint(
            "telegram_bot_id", "idempotency_key", name="uq_deep_link_bot_idempotency"
        ),
    )


def downgrade() -> None:
    op.drop_table("deep_link_registry")
