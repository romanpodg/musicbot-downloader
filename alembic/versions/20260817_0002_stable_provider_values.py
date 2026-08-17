"""Store stable lowercase provider values.

Revision ID: 20260817_0002
Revises: 20260817_0001
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260817_0002"
down_revision: str | None = "20260817_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    track_sources = sa.table("track_sources", sa.column("provider", sa.String(32)))
    op.execute(track_sources.update().values(provider=sa.func.lower(track_sources.c.provider)))


def downgrade() -> None:
    track_sources = sa.table("track_sources", sa.column("provider", sa.String(32)))
    op.execute(track_sources.update().values(provider=sa.func.upper(track_sources.c.provider)))
