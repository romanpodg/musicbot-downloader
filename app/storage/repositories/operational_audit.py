"""Append-only persistence for bounded operational audit events."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import (
    OperationalAuditActorKind,
    OperationalAuditEventType,
    OperationalAuditTargetKind,
)
from app.storage.models import OperationalAuditEvent

MAX_AUDIT_LIST_LIMIT = 200


class OperationalAuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append_event(
        self,
        *,
        occurred_at: datetime,
        event_type: OperationalAuditEventType,
        actor_kind: OperationalAuditActorKind,
        actor_user_id: int | None,
        target_kind: OperationalAuditTargetKind | None,
        target_id: str | None,
        request_id: str | None,
        details_json: str | None,
    ) -> OperationalAuditEvent:
        event = OperationalAuditEvent(
            occurred_at=occurred_at,
            event_type=event_type,
            actor_kind=actor_kind,
            actor_user_id=actor_user_id,
            target_kind=target_kind,
            target_id=target_id,
            request_id=request_id,
            details_json=details_json,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_recent(
        self,
        *,
        limit: int,
        before_id: int | None = None,
        event_type: OperationalAuditEventType | None = None,
        actor_user_id: int | None = None,
    ) -> list[OperationalAuditEvent]:
        if limit < 1 or limit > MAX_AUDIT_LIST_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_AUDIT_LIST_LIMIT}")
        statement = select(OperationalAuditEvent)
        if before_id is not None:
            statement = statement.where(OperationalAuditEvent.id < before_id)
        if event_type is not None:
            statement = statement.where(OperationalAuditEvent.event_type == event_type)
        if actor_user_id is not None:
            statement = statement.where(OperationalAuditEvent.actor_user_id == actor_user_id)
        rows = await self._session.scalars(
            statement.order_by(
                OperationalAuditEvent.occurred_at.desc(), OperationalAuditEvent.id.desc()
            ).limit(limit)
        )
        return list(rows)

    async def count(self) -> int:
        return int(await self._session.scalar(select(func.count(OperationalAuditEvent.id))) or 0)

    async def latest_occurred_at(self) -> datetime | None:
        return await self._session.scalar(select(func.max(OperationalAuditEvent.occurred_at)))
