"""Add durable Stage 25 provider candidate snapshots and attempt audit."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260831_0017"
down_revision: str | None = "20260831_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "download_provider_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "request_id",
            sa.Integer(),
            sa.ForeignKey("download_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_media_id", sa.String(512), nullable=False),
        sa.Column("match_score", sa.Float(), nullable=False),
        sa.Column("match_method", sa.String(32), nullable=False),
        sa.Column("match_reasons", sa.JSON(), nullable=False),
        sa.Column("media_capabilities", sa.JSON(), nullable=False),
        sa.Column("source_reference", sa.String(2048)),
        sa.Column("identity_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "request_id", "provider", "provider_media_id", name="uq_download_provider_candidate"
        ),
    )
    op.create_index(
        "ix_download_provider_candidates_request", "download_provider_candidates", ["request_id"]
    )
    op.create_index(
        "ix_download_provider_candidates_provider_media",
        "download_provider_candidates",
        ["provider", "provider_media_id"],
    )
    op.create_table(
        "download_provider_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("download_lifecycle_jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            sa.Integer(),
            sa.ForeignKey("download_provider_candidates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider_account_id", sa.String(128)),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("fallback_decision", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_download_provider_attempts_job_number",
        "download_provider_attempts",
        ["job_id", "attempt_number"],
    )
    op.create_index(
        "ix_download_provider_attempts_candidate", "download_provider_attempts", ["candidate_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_download_provider_attempts_candidate", table_name="download_provider_attempts"
    )
    op.drop_index(
        "ix_download_provider_attempts_job_number", table_name="download_provider_attempts"
    )
    op.drop_table("download_provider_attempts")
    op.drop_index(
        "ix_download_provider_candidates_provider_media", table_name="download_provider_candidates"
    )
    op.drop_index(
        "ix_download_provider_candidates_request", table_name="download_provider_candidates"
    )
    op.drop_table("download_provider_candidates")
