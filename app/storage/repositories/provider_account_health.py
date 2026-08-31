"""Durable provider-account health persistence."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import MusicProviderName, ProviderAccountHealthState
from app.services.provider_account_selection import ProviderAccountHealth
from app.storage.models.provider_account_health import ProviderAccountHealthRecord


class ProviderAccountHealthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self, provider: MusicProviderName, account_id: str
    ) -> ProviderAccountHealth | None:
        row = await self._session.scalar(
            select(ProviderAccountHealthRecord).where(
                ProviderAccountHealthRecord.provider == provider.value,
                ProviderAccountHealthRecord.account_id == account_id,
            )
        )
        return _to_domain(row) if row is not None else None

    async def list(self, provider: MusicProviderName) -> tuple[ProviderAccountHealth, ...]:
        rows = await self._session.scalars(
            select(ProviderAccountHealthRecord)
            .where(ProviderAccountHealthRecord.provider == provider.value)
            .order_by(ProviderAccountHealthRecord.id)
        )
        return tuple(_to_domain(row) for row in rows)

    async def upsert(self, health: ProviderAccountHealth, now: datetime) -> ProviderAccountHealth:
        values = {
            "provider": health.provider.value,
            "account_id": health.account_id,
            "health_state": health.state.value,
            "failure_streak": health.failure_streak,
            "last_success_at": health.last_success_at,
            "last_failure_at": health.last_failure_at,
            "cooldown_until": health.cooldown_until,
            "created_at": now,
            "updated_at": now,
        }
        await self._session.execute(
            sqlite_insert(ProviderAccountHealthRecord)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[
                    ProviderAccountHealthRecord.provider,
                    ProviderAccountHealthRecord.account_id,
                ],
                set_={
                    key: value
                    for key, value in values.items()
                    if key not in {"created_at", "provider", "account_id"}
                },
            )
        )
        return health


def _to_domain(row: ProviderAccountHealthRecord) -> ProviderAccountHealth:
    return ProviderAccountHealth(
        provider=MusicProviderName(row.provider),
        account_id=row.account_id,
        state=ProviderAccountHealthState(row.health_state),
        failure_streak=row.failure_streak,
        last_success_at=row.last_success_at,
        last_failure_at=row.last_failure_at,
        cooldown_until=row.cooldown_until,
    )


__all__ = ["ProviderAccountHealthRepository"]
