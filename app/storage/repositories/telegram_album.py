"""Atomic persistence operations for Stage 9.3 album orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import case, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import (
    AlbumItemResolutionStatus,
    AlbumRequestStatus,
    QualityProfile,
    TelegramDeliveryStatus,
)
from app.core.models import AlbumSnapshot
from app.storage.models import (
    TelegramAlbumItem,
    TelegramAlbumRequest,
    TelegramDeliveryRequest,
)


@dataclass(frozen=True, slots=True)
class AlbumAggregate:
    selected: int
    item_failed: int
    attached: int
    delivered: int
    delivery_failed: int
    delivery_cancelled: int
    delivery_active: int


class TelegramAlbumRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, request_id: int) -> TelegramAlbumRequest | None:
        return cast(
            TelegramAlbumRequest | None,
            await self._session.get(TelegramAlbumRequest, request_id),
        )

    async def count_active(self) -> int:
        active_statuses = (
            AlbumRequestStatus.AWAITING_QUALITY,
            AlbumRequestStatus.AWAITING_ACTION,
            AlbumRequestStatus.AWAITING_ALBUM_QUALITY,
            AlbumRequestStatus.SELECTING_TRACKS,
            AlbumRequestStatus.QUEUED,
            AlbumRequestStatus.PROCESSING,
        )
        return int(
            await self._session.scalar(
                select(func.count(TelegramAlbumRequest.id)).where(
                    TelegramAlbumRequest.status.in_(active_statuses)
                )
            )
            or 0
        )

    async def get_by_message(
        self, *, telegram_bot_id: int, telegram_chat_id: int, source_message_id: int
    ) -> TelegramAlbumRequest | None:
        return cast(
            TelegramAlbumRequest | None,
            await self._session.scalar(
                select(TelegramAlbumRequest).where(
                    TelegramAlbumRequest.telegram_bot_id == telegram_bot_id,
                    TelegramAlbumRequest.telegram_chat_id == telegram_chat_id,
                    TelegramAlbumRequest.source_message_id == source_message_id,
                )
            ),
        )

    async def create(
        self,
        *,
        telegram_bot_id: int,
        user_id: int,
        telegram_chat_id: int,
        source_message_id: int,
        snapshot: AlbumSnapshot,
        quality_profile: QualityProfile | None,
        now: datetime,
    ) -> TelegramAlbumRequest:
        request = TelegramAlbumRequest(
            telegram_bot_id=telegram_bot_id,
            user_id=user_id,
            telegram_chat_id=telegram_chat_id,
            source_message_id=source_message_id,
            provider=snapshot.provider,
            provider_album_id=snapshot.provider_album_id,
            title=snapshot.title,
            artist=snapshot.artist,
            release_date=snapshot.release_date,
            duration_ms=snapshot.duration_ms,
            track_count=snapshot.track_count,
            quality_profile=quality_profile,
            status=(
                AlbumRequestStatus.AWAITING_QUALITY
                if quality_profile is None
                else AlbumRequestStatus.AWAITING_ACTION
            ),
        )
        self._session.add(request)
        await self._session.flush()
        self._session.add_all(
            [
                TelegramAlbumItem(
                    album_request_id=request.id,
                    position=item.position,
                    disc_number=item.disc_number,
                    track_number=item.track_number,
                    provider_track_id=item.provider_track_id,
                    title=item.title,
                    artist=item.artist,
                    duration_ms=item.duration_ms,
                    explicit=item.explicit,
                    selected=False,
                    resolution_status=AlbumItemResolutionStatus.PENDING,
                    attempt_count=0,
                    available_at=now,
                )
                for item in snapshot.tracks
            ]
        )
        await self._session.flush()
        return request

    async def list_items(
        self, request_id: int, *, offset: int, limit: int
    ) -> list[TelegramAlbumItem]:
        rows = await self._session.scalars(
            select(TelegramAlbumItem)
            .where(TelegramAlbumItem.album_request_id == request_id)
            .order_by(TelegramAlbumItem.position)
            .offset(offset)
            .limit(limit)
        )
        return list(rows)

    async def count_selected(self, request_id: int) -> int:
        return int(
            await self._session.scalar(
                select(func.count(TelegramAlbumItem.id)).where(
                    TelegramAlbumItem.album_request_id == request_id,
                    TelegramAlbumItem.selected.is_(True),
                )
            )
            or 0
        )

    async def record_card_message(self, *, request_id: int, user_id: int, message_id: int) -> bool:
        result = await self._session.execute(
            update(TelegramAlbumRequest)
            .where(
                TelegramAlbumRequest.id == request_id,
                TelegramAlbumRequest.user_id == user_id,
                TelegramAlbumRequest.card_message_id.is_(None),
                TelegramAlbumRequest.status.in_(
                    (
                        AlbumRequestStatus.AWAITING_QUALITY,
                        AlbumRequestStatus.AWAITING_ACTION,
                        AlbumRequestStatus.AWAITING_ALBUM_QUALITY,
                        AlbumRequestStatus.SELECTING_TRACKS,
                    )
                ),
            )
            .values(card_message_id=message_id)
        )
        return _changed(result)

    async def choose_first_quality(
        self, *, request_id: int, user_id: int, quality: QualityProfile
    ) -> TelegramAlbumRequest | None:
        return await self._transition(
            request_id,
            user_id,
            AlbumRequestStatus.AWAITING_QUALITY,
            AlbumRequestStatus.AWAITING_ACTION,
            quality_profile=quality,
        )

    async def open_quality(self, *, request_id: int, user_id: int) -> TelegramAlbumRequest | None:
        return await self._transition(
            request_id,
            user_id,
            AlbumRequestStatus.AWAITING_ACTION,
            AlbumRequestStatus.AWAITING_ALBUM_QUALITY,
        )

    async def choose_quality(
        self, *, request_id: int, user_id: int, quality: QualityProfile
    ) -> TelegramAlbumRequest | None:
        return await self._transition(
            request_id,
            user_id,
            AlbumRequestStatus.AWAITING_ALBUM_QUALITY,
            AlbumRequestStatus.AWAITING_ACTION,
            quality_profile=quality,
        )

    async def quality_back(self, *, request_id: int, user_id: int) -> TelegramAlbumRequest | None:
        return await self._transition(
            request_id,
            user_id,
            AlbumRequestStatus.AWAITING_ALBUM_QUALITY,
            AlbumRequestStatus.AWAITING_ACTION,
        )

    async def open_selection(self, *, request_id: int, user_id: int) -> TelegramAlbumRequest | None:
        return await self._transition(
            request_id,
            user_id,
            AlbumRequestStatus.AWAITING_ACTION,
            AlbumRequestStatus.SELECTING_TRACKS,
        )

    async def selection_back(self, *, request_id: int, user_id: int) -> TelegramAlbumRequest | None:
        return await self._transition(
            request_id,
            user_id,
            AlbumRequestStatus.SELECTING_TRACKS,
            AlbumRequestStatus.AWAITING_ACTION,
        )

    async def toggle_item(self, *, request_id: int, item_id: int, user_id: int) -> bool:
        owned_selecting = select(TelegramAlbumRequest.id).where(
            TelegramAlbumRequest.id == request_id,
            TelegramAlbumRequest.user_id == user_id,
            TelegramAlbumRequest.status == AlbumRequestStatus.SELECTING_TRACKS,
        )
        result = await self._session.execute(
            update(TelegramAlbumItem)
            .where(
                TelegramAlbumItem.id == item_id,
                TelegramAlbumItem.album_request_id == request_id,
                TelegramAlbumItem.album_request_id.in_(owned_selecting),
            )
            .values(selected=~TelegramAlbumItem.selected)
        )
        return _changed(result)

    async def set_all_selected(self, *, request_id: int, user_id: int, selected: bool) -> bool:
        request = await self.get(request_id)
        if (
            request is None
            or request.user_id != user_id
            or request.status is not AlbumRequestStatus.SELECTING_TRACKS
        ):
            return False
        await self._session.execute(
            update(TelegramAlbumItem)
            .where(TelegramAlbumItem.album_request_id == request_id)
            .values(selected=selected)
        )
        return True

    async def queue_all(
        self, *, request_id: int, user_id: int, now: datetime
    ) -> TelegramAlbumRequest | None:
        changed = await self._transition(
            request_id,
            user_id,
            AlbumRequestStatus.AWAITING_ACTION,
            AlbumRequestStatus.QUEUED,
            started_at=now,
        )
        if changed is not None:
            await self._session.execute(
                update(TelegramAlbumItem)
                .where(TelegramAlbumItem.album_request_id == request_id)
                .values(selected=True)
            )
        return changed

    async def queue_selected(
        self, *, request_id: int, user_id: int, now: datetime
    ) -> tuple[TelegramAlbumRequest | None, bool]:
        if await self.count_selected(request_id) == 0:
            return None, True
        changed = await self._transition(
            request_id,
            user_id,
            AlbumRequestStatus.SELECTING_TRACKS,
            AlbumRequestStatus.QUEUED,
            started_at=now,
        )
        return changed, False

    async def recover_expired(self, *, now: datetime, max_attempts: int) -> int:
        result = await self._session.execute(
            update(TelegramAlbumItem)
            .where(
                TelegramAlbumItem.resolution_status == AlbumItemResolutionStatus.RESOLVING,
                TelegramAlbumItem.lease_expires_at <= now,
            )
            .values(
                resolution_status=case(
                    (
                        TelegramAlbumItem.attempt_count >= max_attempts,
                        AlbumItemResolutionStatus.FAILED,
                    ),
                    else_=AlbumItemResolutionStatus.PENDING,
                ),
                available_at=now,
                lease_owner=None,
                lease_expires_at=None,
                last_error_code="ALBUM_ITEM_LEASE_EXPIRED",
            )
        )
        return cast(CursorResult[Any], result).rowcount

    async def count_expired_leases(self, now: datetime) -> int:
        return int(
            await self._session.scalar(
                select(func.count(TelegramAlbumItem.id)).where(
                    TelegramAlbumItem.resolution_status == AlbumItemResolutionStatus.RESOLVING,
                    TelegramAlbumItem.lease_expires_at <= now,
                )
            )
            or 0
        )

    async def count_aggregate_reconciliation_candidates(self) -> int:
        count = 0
        for request in await self.list_reconcilable():
            aggregate = await self.aggregate(request.id)
            unresolved = aggregate.selected - aggregate.item_failed - aggregate.attached
            terminal = (
                aggregate.delivered + aggregate.delivery_failed + aggregate.delivery_cancelled
            )
            if (
                unresolved <= 0
                and aggregate.delivery_active == 0
                and terminal == aggregate.attached
            ):
                count += 1
        return count

    async def claim_item(
        self, *, worker_id: str, now: datetime, lease_expires_at: datetime
    ) -> TelegramAlbumItem | None:
        active_album = select(TelegramAlbumRequest.id).where(
            TelegramAlbumRequest.id == TelegramAlbumItem.album_request_id,
            TelegramAlbumRequest.status.in_(
                (AlbumRequestStatus.QUEUED, AlbumRequestStatus.PROCESSING)
            ),
        )
        candidate = (
            select(TelegramAlbumItem.id)
            .where(
                TelegramAlbumItem.album_request_id.in_(active_album),
                TelegramAlbumItem.selected.is_(True),
                TelegramAlbumItem.resolution_status == AlbumItemResolutionStatus.PENDING,
                TelegramAlbumItem.available_at <= now,
            )
            .order_by(TelegramAlbumItem.album_request_id, TelegramAlbumItem.position)
            .limit(1)
            .scalar_subquery()
        )
        result = await self._session.execute(
            update(TelegramAlbumItem)
            .where(
                TelegramAlbumItem.id == candidate,
                TelegramAlbumItem.resolution_status == AlbumItemResolutionStatus.PENDING,
            )
            .values(
                resolution_status=AlbumItemResolutionStatus.RESOLVING,
                attempt_count=TelegramAlbumItem.attempt_count + 1,
                lease_owner=worker_id,
                lease_expires_at=lease_expires_at,
            )
            .returning(TelegramAlbumItem)
        )
        item = result.scalar_one_or_none()
        if item is not None:
            await self._session.execute(
                update(TelegramAlbumRequest)
                .where(
                    TelegramAlbumRequest.id == item.album_request_id,
                    TelegramAlbumRequest.status == AlbumRequestStatus.QUEUED,
                )
                .values(status=AlbumRequestStatus.PROCESSING)
            )
        return item

    async def attach(self, *, item_id: int, worker_id: str, track_id: int) -> bool:
        result = await self._session.execute(
            update(TelegramAlbumItem)
            .where(
                TelegramAlbumItem.id == item_id,
                TelegramAlbumItem.resolution_status == AlbumItemResolutionStatus.RESOLVING,
                TelegramAlbumItem.lease_owner == worker_id,
            )
            .values(
                resolution_status=AlbumItemResolutionStatus.ATTACHED,
                track_id=track_id,
                lease_owner=None,
                lease_expires_at=None,
                last_error_code=None,
            )
        )
        return _changed(result)

    async def retry_or_fail(
        self,
        *,
        item_id: int,
        worker_id: str,
        retryable: bool,
        max_attempts: int,
        available_at: datetime,
        error_code: str,
    ) -> AlbumItemResolutionStatus | None:
        item = cast(TelegramAlbumItem | None, await self._session.get(TelegramAlbumItem, item_id))
        if (
            item is None
            or item.resolution_status is not AlbumItemResolutionStatus.RESOLVING
            or item.lease_owner != worker_id
        ):
            return None
        status = (
            AlbumItemResolutionStatus.PENDING
            if retryable and item.attempt_count < max_attempts
            else AlbumItemResolutionStatus.FAILED
        )
        item.resolution_status = status
        item.available_at = available_at
        item.last_error_code = error_code
        item.lease_owner = None
        item.lease_expires_at = None
        await self._session.flush()
        return status

    async def aggregate(self, request_id: int) -> AlbumAggregate:
        row = (
            await self._session.execute(
                select(
                    func.count(TelegramAlbumItem.id),
                    func.count(TelegramAlbumItem.id).filter(
                        TelegramAlbumItem.resolution_status == AlbumItemResolutionStatus.FAILED
                    ),
                    func.count(TelegramDeliveryRequest.id),
                    func.count(TelegramDeliveryRequest.id).filter(
                        TelegramDeliveryRequest.status == TelegramDeliveryStatus.DELIVERED
                    ),
                    func.count(TelegramDeliveryRequest.id).filter(
                        TelegramDeliveryRequest.status == TelegramDeliveryStatus.FAILED
                    ),
                    func.count(TelegramDeliveryRequest.id).filter(
                        TelegramDeliveryRequest.status == TelegramDeliveryStatus.CANCELLED
                    ),
                    func.count(TelegramDeliveryRequest.id).filter(
                        TelegramDeliveryRequest.status.not_in(
                            (
                                TelegramDeliveryStatus.DELIVERED,
                                TelegramDeliveryStatus.FAILED,
                                TelegramDeliveryStatus.CANCELLED,
                            )
                        )
                    ),
                )
                .outerjoin(
                    TelegramDeliveryRequest,
                    TelegramDeliveryRequest.album_item_id == TelegramAlbumItem.id,
                )
                .where(
                    TelegramAlbumItem.album_request_id == request_id,
                    TelegramAlbumItem.selected.is_(True),
                )
            )
        ).one()
        return AlbumAggregate(*(int(value or 0) for value in row))

    async def mark_terminal(
        self, *, request_id: int, status: AlbumRequestStatus, now: datetime
    ) -> TelegramAlbumRequest | None:
        result = await self._session.execute(
            update(TelegramAlbumRequest)
            .where(
                TelegramAlbumRequest.id == request_id,
                TelegramAlbumRequest.status.in_(
                    (AlbumRequestStatus.QUEUED, AlbumRequestStatus.PROCESSING)
                ),
            )
            .values(status=status, completed_at=now)
            .returning(TelegramAlbumRequest)
        )
        return result.scalar_one_or_none()

    async def list_reconcilable(self, limit: int = 100) -> list[TelegramAlbumRequest]:
        rows = await self._session.scalars(
            select(TelegramAlbumRequest)
            .where(
                TelegramAlbumRequest.status.in_(
                    (AlbumRequestStatus.QUEUED, AlbumRequestStatus.PROCESSING)
                )
            )
            .order_by(TelegramAlbumRequest.updated_at, TelegramAlbumRequest.id)
            .limit(limit)
        )
        return list(rows)

    async def list_unnotified_terminal(self, limit: int = 100) -> list[TelegramAlbumRequest]:
        rows = await self._session.scalars(
            select(TelegramAlbumRequest)
            .where(
                TelegramAlbumRequest.status.in_(
                    (
                        AlbumRequestStatus.COMPLETED,
                        AlbumRequestStatus.PARTIALLY_FAILED,
                        AlbumRequestStatus.FAILED,
                    )
                ),
                TelegramAlbumRequest.completion_notified_at.is_(None),
            )
            .order_by(TelegramAlbumRequest.completed_at, TelegramAlbumRequest.id)
            .limit(limit)
        )
        return list(rows)

    async def mark_notified(self, *, request_id: int, message_id: int, now: datetime) -> bool:
        result = await self._session.execute(
            update(TelegramAlbumRequest)
            .where(
                TelegramAlbumRequest.id == request_id,
                TelegramAlbumRequest.completion_notified_at.is_(None),
            )
            .values(completion_message_id=message_id, completion_notified_at=now)
        )
        return _changed(result)

    async def _transition(
        self,
        request_id: int,
        user_id: int,
        expected: AlbumRequestStatus,
        target: AlbumRequestStatus,
        **values: object,
    ) -> TelegramAlbumRequest | None:
        result = await self._session.execute(
            update(TelegramAlbumRequest)
            .where(
                TelegramAlbumRequest.id == request_id,
                TelegramAlbumRequest.user_id == user_id,
                TelegramAlbumRequest.status == expected,
            )
            .values(status=target, last_error_code=None, **values)
            .returning(TelegramAlbumRequest)
        )
        return result.scalar_one_or_none()


def _changed(result: Any) -> bool:
    return bool(cast(CursorResult[Any], result).rowcount)
