"""Add nullable durable routing for one Stage 28 batch parent message."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260902_0019"
down_revision: str | None = "20260901_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "batch_download_requests", sa.Column("telegram_bot_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "batch_download_requests", sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "batch_download_requests", sa.Column("parent_message_id", sa.BigInteger(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("batch_download_requests", "parent_message_id")
    op.drop_column("batch_download_requests", "telegram_chat_id")
    op.drop_column("batch_download_requests", "telegram_bot_id")
