"""Add Stage 22 preferences and immutable request profile snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260830_0014"
down_revision: str | None = "20260830_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_download_preferences",
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("quality", sa.String(32), nullable=False),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("delivery_mode", sa.String(16), nullable=False),
        sa.Column("embed_metadata", sa.Boolean(), nullable=False),
        sa.Column("embed_cover", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "quality IN ('best_available','lossless','high','standard')",
            name="ck_user_download_preferences_quality",
        ),
        sa.CheckConstraint(
            "format IN ('original','flac','mp3','m4a')",
            name="ck_user_download_preferences_format",
        ),
        sa.CheckConstraint(
            "delivery_mode IN ('audio','document')",
            name="ck_user_download_preferences_delivery_mode",
        ),
        sa.CheckConstraint(
            "NOT (quality = 'lossless' AND format IN ('mp3','m4a'))",
            name="ck_user_download_preferences_quality_format",
        ),
    )
    op.create_index(
        "ix_user_download_preferences_updated", "user_download_preferences", ["updated_at"]
    )
    for name, type_ in (
        ("requested_quality", sa.String(32)),
        ("effective_quality", sa.String(32)),
        ("requested_format", sa.String(16)),
        ("effective_format", sa.String(16)),
        ("delivery_mode", sa.String(16)),
        ("embed_metadata", sa.Boolean()),
        ("embed_cover", sa.Boolean()),
        ("profile_fallback_applied", sa.Boolean()),
        ("profile_fallback_reason", sa.String(64)),
    ):
        op.add_column("download_requests", sa.Column(name, type_, nullable=True))
    op.create_index(
        "ix_download_requests_profile_quality", "download_requests", ["effective_quality"]
    )


def downgrade() -> None:
    op.drop_index("ix_download_requests_profile_quality", table_name="download_requests")
    for name in (
        "profile_fallback_reason",
        "profile_fallback_applied",
        "embed_cover",
        "embed_metadata",
        "delivery_mode",
        "effective_format",
        "requested_format",
        "effective_quality",
        "requested_quality",
    ):
        op.drop_column("download_requests", name)
    op.drop_index("ix_user_download_preferences_updated", table_name="user_download_preferences")
    op.drop_table("user_download_preferences")
