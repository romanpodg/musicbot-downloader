"""Enable the persisted AAC_128 exact quality profile."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260906_0020"
down_revision: str | None = "20260902_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "download_jobs",
    "upload_jobs",
    "download_flights",
    "telegram_file_cache",
    "telegram_delivery_requests",
    "telegram_album_requests",
)
_OLD_VALUES = "'MP3_128', 'MP3_320', 'AAC_256', 'LOSSLESS'"
_NEW_VALUES = "'MP3_128', 'MP3_320', 'AAC_128', 'AAC_256', 'LOSSLESS'"


def _constraint(table: str) -> str:
    return {
        "download_jobs": "ck_download_jobs_quality_profile",
        "upload_jobs": "ck_upload_jobs_quality_profile",
        "download_flights": "ck_download_flights_quality_profile",
        "telegram_file_cache": "ck_telegram_file_cache_quality_profile",
        "telegram_delivery_requests": "ck_telegram_delivery_quality_profile",
        "telegram_album_requests": "ck_telegram_album_quality_profile",
    }[table]


def _replace(table: str, values: str, *, nullable: bool) -> None:
    expression = (
        f"quality_profile IS NULL OR quality_profile IN ({values})"
        if nullable
        else f"quality_profile IN ({values})"
    )
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(_constraint(table), type_="check")
        batch.create_check_constraint(_constraint(table), expression)


def upgrade() -> None:
    for table in _TABLES:
        _replace(
            table,
            _NEW_VALUES,
            nullable=table in {"telegram_delivery_requests", "telegram_album_requests"},
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        found = bind.execute(
            sa.text(f"SELECT 1 FROM {table} WHERE quality_profile = 'AAC_128' LIMIT 1")
        ).first()
        if found is not None:
            raise RuntimeError(
                f"cannot downgrade Stage 30.2 quality schema: {table} contains AAC_128 rows"
            )
    for table in _TABLES:
        _replace(
            table,
            _OLD_VALUES,
            nullable=table in {"telegram_delivery_requests", "telegram_album_requests"},
        )
