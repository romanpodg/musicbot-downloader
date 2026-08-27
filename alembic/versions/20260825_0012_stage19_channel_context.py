"""Add Stage 19 chat policy, channel binding, and explicit delivery targets.

Revision ID: 20260825_0012
Revises: 20260820_0011
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260825_0012"
down_revision: str | None = "20260820_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_chat_policies",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("allow_downloads", sa.Boolean(), nullable=False),
        sa.Column("delivery_mode", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("delivery_mode IN ('USER', 'CHAT')", name="ck_chat_policy_mode"),
        sa.PrimaryKeyConstraint("chat_id"),
    )
    op.create_table(
        "telegram_channel_bindings",
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('CONNECTED', 'NO_PERMISSION', 'DISCONNECTED')",
            name="ck_channel_binding_status",
        ),
        sa.PrimaryKeyConstraint("channel_id"),
    )
    with op.batch_alter_table("telegram_delivery_requests") as batch_op:
        batch_op.add_column(sa.Column("delivery_chat_id", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("delivery_target_type", sa.String(length=16), nullable=True))
    op.execute(
        "UPDATE telegram_delivery_requests "
        "SET delivery_chat_id = telegram_chat_id, delivery_target_type = 'PRIVATE_USER'"
    )
    with op.batch_alter_table("telegram_delivery_requests") as batch_op:
        batch_op.create_check_constraint(
            "ck_telegram_delivery_target_type",
            "delivery_target_type IN ('PRIVATE_USER', 'GROUP_CHAT', 'CHANNEL')",
        )


def downgrade() -> None:
    with op.batch_alter_table("telegram_delivery_requests") as batch_op:
        batch_op.drop_constraint("ck_telegram_delivery_target_type", type_="check")
        batch_op.drop_column("delivery_target_type")
        batch_op.drop_column("delivery_chat_id")
    op.drop_table("telegram_channel_bindings")
    op.drop_table("telegram_chat_policies")
