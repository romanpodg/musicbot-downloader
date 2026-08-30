"""Add durable Stage 23 album/playlist batch orchestration records."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260830_0015"
down_revision: str | None = "20260830_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "batch_download_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "requester_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("confirmation_id", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("source_collection_id", sa.String(512), nullable=False),
        sa.Column("source_reference", sa.String(1024), nullable=False),
        sa.Column("title", sa.String(1024), nullable=False),
        sa.Column("creator", sa.String(1024)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=False),
        sa.Column(
            "parent_batch_id",
            sa.Integer(),
            sa.ForeignKey("batch_download_requests.id", ondelete="RESTRICT"),
        ),
        sa.Column("retry_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requested_quality", sa.String(32)),
        sa.Column("requested_format", sa.String(16)),
        sa.Column("delivery_mode", sa.String(16)),
        sa.Column("embed_metadata", sa.Boolean()),
        sa.Column("embed_cover", sa.Boolean()),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("confirmation_id", name="uq_batch_download_requests_confirmation"),
        sa.UniqueConstraint(
            "parent_batch_id", "retry_generation", name="uq_batch_download_requests_retry"
        ),
        sa.CheckConstraint("total_items >= 1", name="ck_batch_download_requests_total_items"),
        sa.CheckConstraint(
            "source_type IN ('album','playlist')", name="ck_batch_download_requests_source_type"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','EXPANDING','ACTIVE','COMPLETED','PARTIAL','FAILED','CANCELLED')",
            name="ck_batch_download_requests_status",
        ),
    )
    op.create_index(
        "ix_batch_download_requests_user_created",
        "batch_download_requests",
        ["requester_user_id", "created_at"],
    )
    op.create_index("ix_batch_download_requests_status", "batch_download_requests", ["status"])
    op.create_index(
        "ix_batch_download_requests_parent", "batch_download_requests", ["parent_batch_id"]
    )
    op.create_table(
        "batch_download_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("batch_download_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("provider_media_id", sa.String(512), nullable=False),
        sa.Column("source_reference", sa.String(1024)),
        sa.Column("title", sa.String(1024)),
        sa.Column("artist", sa.String(1024)),
        sa.Column("duration_ms", sa.BigInteger()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "download_request_id",
            sa.Integer(),
            sa.ForeignKey("download_requests.id", ondelete="SET NULL"),
        ),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.String(256)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("batch_id", "position", name="uq_batch_download_items_position"),
        sa.UniqueConstraint("download_request_id", name="uq_batch_download_items_download_request"),
        sa.CheckConstraint("position >= 1", name="ck_batch_download_items_position"),
        sa.CheckConstraint(
            "status IN ('PENDING','ADMITTED','SKIPPED','FAILED')",
            name="ck_batch_download_items_status",
        ),
    )
    op.create_index(
        "ix_batch_download_items_batch_position", "batch_download_items", ["batch_id", "position"]
    )
    op.create_index(
        "ix_batch_download_items_request", "batch_download_items", ["download_request_id"]
    )
    op.create_index("ix_batch_download_items_status", "batch_download_items", ["status"])


def downgrade() -> None:
    op.drop_index("ix_batch_download_items_status", table_name="batch_download_items")
    op.drop_index("ix_batch_download_items_request", table_name="batch_download_items")
    op.drop_index("ix_batch_download_items_batch_position", table_name="batch_download_items")
    op.drop_table("batch_download_items")
    op.drop_index("ix_batch_download_requests_parent", table_name="batch_download_requests")
    op.drop_index("ix_batch_download_requests_status", table_name="batch_download_requests")
    op.drop_index("ix_batch_download_requests_user_created", table_name="batch_download_requests")
    op.drop_table("batch_download_requests")
