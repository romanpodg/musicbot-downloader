"""Add Stage 24 history snapshots, replay provenance, and artifact cache."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260831_0016"
down_revision: str | None = "20260830_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name, type_ in (
        ("media_title", sa.String(1024)),
        ("media_artist", sa.String(1024)),
        ("media_album", sa.String(1024)),
        ("replay_of_request_id", sa.Integer()),
    ):
        op.add_column("download_requests", sa.Column(name, type_, nullable=True))
    with op.batch_alter_table("download_requests", recreate="always") as batch:
        batch.create_foreign_key(
            "fk_download_requests_replay_of",
            "download_requests",
            ["replay_of_request_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index("ix_download_requests_replay_of", "download_requests", ["replay_of_request_id"])
    op.create_table(
        "telegram_artifact_cache_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_media_id", sa.String(512), nullable=False),
        sa.Column("effective_quality", sa.String(32), nullable=False),
        sa.Column("effective_format", sa.String(16), nullable=False),
        sa.Column("delivery_mode", sa.String(16), nullable=False),
        sa.Column("embed_metadata", sa.Boolean(), nullable=False),
        sa.Column("embed_cover", sa.Boolean(), nullable=False),
        sa.Column("artifact_processing_version", sa.Integer(), nullable=False),
        sa.Column("telegram_file_id", sa.String(512), nullable=False),
        sa.Column("telegram_file_unique_id", sa.String(512)),
        sa.Column(
            "source_delivery_id",
            sa.Integer(),
            sa.ForeignKey("download_deliveries.id", ondelete="SET NULL"),
        ),
        sa.Column("file_size", sa.BigInteger()),
        sa.Column("mime_type", sa.String(128)),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
        sa.Column("invalidation_reason", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("fingerprint", name="uq_telegram_artifact_cache_fingerprint"),
    )
    op.create_index(
        "ix_telegram_artifact_cache_active_used",
        "telegram_artifact_cache_entries",
        ["invalidated_at", "last_used_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_telegram_artifact_cache_active_used", table_name="telegram_artifact_cache_entries"
    )
    op.drop_table("telegram_artifact_cache_entries")
    op.drop_index("ix_download_requests_replay_of", table_name="download_requests")
    with op.batch_alter_table("download_requests", recreate="always") as batch:
        batch.drop_constraint("fk_download_requests_replay_of", type_="foreignkey")
    for name in ("replay_of_request_id", "media_album", "media_artist", "media_title"):
        op.drop_column("download_requests", name)
