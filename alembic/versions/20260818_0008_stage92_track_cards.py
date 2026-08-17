"""Add Stage 9.2 Track Card request states and message identity.

Revision ID: 20260818_0008
Revises: 20260818_0007
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260818_0008"
down_revision: str | None = "20260818_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("telegram_delivery_requests") as batch_op:
        batch_op.add_column(sa.Column("card_message_id", sa.BigInteger(), nullable=True))
        batch_op.drop_constraint("ck_telegram_delivery_status", type_="check")
        batch_op.create_check_constraint(
            "ck_telegram_delivery_status",
            "status IN ('AWAITING_QUALITY', 'AWAITING_ACTION', "
            "'AWAITING_TRACK_QUALITY', 'QUEUED', 'WAITING', 'SENDING', "
            "'DELIVERED', 'FAILED', 'CANCELLED')",
        )


def downgrade() -> None:
    with op.batch_alter_table("telegram_delivery_requests") as batch_op:
        batch_op.drop_constraint("ck_telegram_delivery_status", type_="check")
        batch_op.create_check_constraint(
            "ck_telegram_delivery_status",
            "status IN ('AWAITING_QUALITY', 'QUEUED', 'WAITING', 'SENDING', "
            "'DELIVERED', 'FAILED', 'CANCELLED')",
        )
        batch_op.drop_column("card_message_id")
