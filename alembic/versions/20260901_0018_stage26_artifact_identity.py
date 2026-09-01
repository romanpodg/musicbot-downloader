"""Add compact Stage 26 artifact identity to technical/cache boundaries."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260901_0018"
down_revision: str | None = "20260831_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("download_jobs", "upload_jobs", "download_flights", "telegram_file_cache"):
        op.add_column(table, sa.Column("artifact_fingerprint", sa.String(64), nullable=True))
        op.create_index(f"ix_{table}_artifact_fingerprint", table, ["artifact_fingerprint"])
        # Legacy rows retain a deterministic quality-only identity.  New Stage 26
        # admissions overwrite this with the full immutable artifact fingerprint.
        op.execute(
            sa.text(
                f"UPDATE {table} SET artifact_fingerprint = "
                "'legacy:' || lower(quality_profile) WHERE artifact_fingerprint IS NULL"
            )
        )
    with op.batch_alter_table("download_flights") as batch:
        batch.drop_constraint("uq_download_flights_key", type_="unique")
        batch.create_unique_constraint(
            "uq_download_flights_key", ["track_id", "quality_profile", "artifact_fingerprint"]
        )
    with op.batch_alter_table("telegram_file_cache") as batch:
        batch.drop_constraint("uq_telegram_file_cache_key", type_="unique")
        batch.create_unique_constraint(
            "uq_telegram_file_cache_key",
            ["telegram_bot_id", "track_id", "quality_profile", "artifact_fingerprint"],
        )


def downgrade() -> None:
    op.drop_index("ix_telegram_file_cache_artifact_fingerprint", table_name="telegram_file_cache")
    with op.batch_alter_table("telegram_file_cache") as batch:
        batch.drop_constraint("uq_telegram_file_cache_key", type_="unique")
        batch.drop_column("artifact_fingerprint")
        batch.create_unique_constraint(
            "uq_telegram_file_cache_key", ["telegram_bot_id", "track_id", "quality_profile"]
        )
    for table in ("upload_jobs", "download_jobs"):
        op.drop_index(f"ix_{table}_artifact_fingerprint", table_name=table)
        op.drop_column(table, "artifact_fingerprint")
    op.drop_index("ix_download_flights_artifact_fingerprint", table_name="download_flights")
    with op.batch_alter_table("download_flights") as batch:
        batch.drop_constraint("uq_download_flights_key", type_="unique")
        batch.drop_column("artifact_fingerprint")
        batch.create_unique_constraint("uq_download_flights_key", ["track_id", "quality_profile"])
