"""Stage 12.3 operational audit history.

Revision ID: 20260820_0011
Revises: 20260820_0010
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260820_0011"
down_revision: str | None = "20260820_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operational_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("actor_kind", sa.String(length=24), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("target_kind", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "event_type IN ('ADMIN_PROMOTED', 'ADMIN_DEMOTED', "
            "'WORKER_DESIRED_CHANGED', 'DEEP_LINK_REGISTERED', 'DEEP_LINK_REVOKED', "
            "'CRASH_RECOVERY_COMPLETED', 'MANUAL_RECOVERY_EXECUTED', "
            "'MANUAL_ARTIFACT_CLEANUP_EXECUTED', 'SQLITE_BACKUP_CREATED')",
            name="ck_operational_audit_event_type",
        ),
        sa.CheckConstraint(
            "actor_kind IN ('TELEGRAM_USER', 'INTERNAL_API', 'LOCAL_OPERATOR', 'SYSTEM')",
            name="ck_operational_audit_actor_kind",
        ),
        sa.CheckConstraint(
            "target_kind IS NULL OR target_kind IN ('USER', 'WORKER_POOL', 'DEEP_LINK', "
            "'RECOVERY', 'ARTIFACT_CLEANUP', 'DATABASE_BACKUP')",
            name="ck_operational_audit_target_kind",
        ),
        sa.CheckConstraint(
            "(actor_kind = 'TELEGRAM_USER' AND actor_user_id IS NOT NULL) OR "
            "(actor_kind != 'TELEGRAM_USER' AND actor_user_id IS NULL)",
            name="ck_operational_audit_actor_identity",
        ),
        sa.CheckConstraint(
            "details_json IS NULL OR length(details_json) <= 4096",
            name="ck_operational_audit_details_length",
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_operational_audit_occurred", "operational_audit_events", ["occurred_at"])
    op.create_index(
        "ix_operational_audit_event_occurred",
        "operational_audit_events",
        ["event_type", "occurred_at"],
    )
    op.create_index(
        "ix_operational_audit_actor_occurred",
        "operational_audit_events",
        ["actor_user_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_operational_audit_actor_occurred", table_name="operational_audit_events")
    op.drop_index("ix_operational_audit_event_occurred", table_name="operational_audit_events")
    op.drop_index("ix_operational_audit_occurred", table_name="operational_audit_events")
    op.drop_table("operational_audit_events")
