"""Atomic persistence operations for the Stage 9 delivery outbox."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.delivery_targets import DeliveryTarget, DeliveryTargetType, PrivateUserTarget
from app.core.enums import QualityProfile, SubscriberStatus, TelegramDeliveryStatus
from app.storage.models import JobSubscriber, TelegramDeliveryRequest


class TelegramDeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, request_id: int) -> TelegramDeliveryRequest | None:
        return cast(
            TelegramDeliveryRequest | None,
            await self._session.get(TelegramDeliveryRequest, request_id),
        )

    async def status_counts(self) -> dict[TelegramDeliveryStatus, int]:
        rows = await self._session.execute(
            select(TelegramDeliveryRequest.status, func.count(TelegramDeliveryRequest.id)).group_by(
                TelegramDeliveryRequest.status
            )
        )
        return {status: int(count) for status, count in rows}

    async def get_by_message(
        self, *, telegram_bot_id: int, telegram_chat_id: int, source_message_id: int
    ) -> TelegramDeliveryRequest | None:
        return cast(
            TelegramDeliveryRequest | None,
            await self._session.scalar(
                select(TelegramDeliveryRequest).where(
                    TelegramDeliveryRequest.telegram_bot_id == telegram_bot_id,
                    TelegramDeliveryRequest.telegram_chat_id == telegram_chat_id,
                    TelegramDeliveryRequest.source_message_id == source_message_id,
                )
            ),
        )

    async def get_by_album_item(self, album_item_id: int) -> TelegramDeliveryRequest | None:
        return cast(
            TelegramDeliveryRequest | None,
            await self._session.scalar(
                select(TelegramDeliveryRequest).where(
                    TelegramDeliveryRequest.album_item_id == album_item_id
                )
            ),
        )

    async def create(
        self,
        *,
        telegram_bot_id: int,
        user_id: int,
        telegram_chat_id: int,
        delivery_target: DeliveryTarget | None = None,
        source_message_id: int,
        track_id: int,
        quality_profile: QualityProfile | None,
        status: TelegramDeliveryStatus,
        now: datetime,
    ) -> TelegramDeliveryRequest:
        target = delivery_target or PrivateUserTarget(telegram_chat_id)
        request = TelegramDeliveryRequest(
            telegram_bot_id=telegram_bot_id,
            user_id=user_id,
            telegram_chat_id=telegram_chat_id,
            delivery_chat_id=target.chat_id,
            delivery_target_type=target.target_type,
            source_message_id=source_message_id,
            track_id=track_id,
            quality_profile=quality_profile,
            status=status,
            available_at=now,
        )
        self._session.add(request)
        await self._session.flush()
        return request

    async def create_album_child(
        self,
        *,
        telegram_bot_id: int,
        user_id: int,
        telegram_chat_id: int,
        album_item_id: int,
        track_id: int,
        quality_profile: QualityProfile,
        now: datetime,
    ) -> TelegramDeliveryRequest:
        existing = await self.get_by_album_item(album_item_id)
        if existing is not None:
            return existing
        request = TelegramDeliveryRequest(
            telegram_bot_id=telegram_bot_id,
            user_id=user_id,
            telegram_chat_id=telegram_chat_id,
            delivery_chat_id=telegram_chat_id,
            delivery_target_type=DeliveryTargetType.PRIVATE_USER,
            source_message_id=None,
            album_item_id=album_item_id,
            track_id=track_id,
            quality_profile=quality_profile,
            status=TelegramDeliveryStatus.QUEUED,
            available_at=now,
        )
        self._session.add(request)
        await self._session.flush()
        return request

    async def choose_quality(
        self,
        *,
        request_id: int,
        user_id: int,
        quality_profile: QualityProfile,
        now: datetime,
        telegram_chat_id: int | None = None,
    ) -> TelegramDeliveryRequest | None:
        conditions = [
            TelegramDeliveryRequest.id == request_id,
            TelegramDeliveryRequest.user_id == user_id,
            TelegramDeliveryRequest.status == TelegramDeliveryStatus.AWAITING_QUALITY,
        ]
        if telegram_chat_id is not None:
            conditions.append(TelegramDeliveryRequest.telegram_chat_id == telegram_chat_id)
        result = await self._session.execute(
            update(TelegramDeliveryRequest)
            .where(*conditions)
            .values(
                quality_profile=quality_profile,
                status=TelegramDeliveryStatus.QUEUED,
                available_at=now,
                last_error_code=None,
            )
            .returning(TelegramDeliveryRequest)
        )
        return result.scalar_one_or_none()

    async def start_default_quality(
        self, *, request_id: int, user_id: int, now: datetime, telegram_chat_id: int | None = None
    ) -> TelegramDeliveryRequest | None:
        return await self._transition(
            request_id=request_id,
            user_id=user_id,
            expected=TelegramDeliveryStatus.AWAITING_ACTION,
            target=TelegramDeliveryStatus.QUEUED,
            now=now,
            telegram_chat_id=telegram_chat_id,
        )

    async def open_track_quality(
        self, *, request_id: int, user_id: int, now: datetime, telegram_chat_id: int | None = None
    ) -> TelegramDeliveryRequest | None:
        return await self._transition(
            request_id=request_id,
            user_id=user_id,
            expected=TelegramDeliveryStatus.AWAITING_ACTION,
            target=TelegramDeliveryStatus.AWAITING_TRACK_QUALITY,
            now=now,
            telegram_chat_id=telegram_chat_id,
        )

    async def choose_track_quality(
        self,
        *,
        request_id: int,
        user_id: int,
        quality_profile: QualityProfile,
        now: datetime,
        telegram_chat_id: int | None = None,
    ) -> TelegramDeliveryRequest | None:
        return await self._transition(
            request_id=request_id,
            user_id=user_id,
            expected=TelegramDeliveryStatus.AWAITING_TRACK_QUALITY,
            target=TelegramDeliveryStatus.QUEUED,
            now=now,
            quality_profile=quality_profile,
            telegram_chat_id=telegram_chat_id,
        )

    async def back_to_action(
        self, *, request_id: int, user_id: int, now: datetime, telegram_chat_id: int | None = None
    ) -> TelegramDeliveryRequest | None:
        return await self._transition(
            request_id=request_id,
            user_id=user_id,
            expected=TelegramDeliveryStatus.AWAITING_TRACK_QUALITY,
            target=TelegramDeliveryStatus.AWAITING_ACTION,
            now=now,
            telegram_chat_id=telegram_chat_id,
        )

    async def record_card_message(
        self,
        *,
        request_id: int,
        user_id: int,
        message_id: int,
        telegram_chat_id: int | None = None,
    ) -> bool:
        conditions = [
            TelegramDeliveryRequest.id == request_id,
            TelegramDeliveryRequest.user_id == user_id,
            TelegramDeliveryRequest.card_message_id.is_(None),
            TelegramDeliveryRequest.status.in_(
                (
                    TelegramDeliveryStatus.AWAITING_QUALITY,
                    TelegramDeliveryStatus.AWAITING_ACTION,
                    TelegramDeliveryStatus.AWAITING_TRACK_QUALITY,
                )
            ),
        ]
        if telegram_chat_id is not None:
            conditions.append(TelegramDeliveryRequest.telegram_chat_id == telegram_chat_id)
        result = await self._session.execute(
            update(TelegramDeliveryRequest).where(*conditions).values(card_message_id=message_id)
        )
        return _changed(result)

    async def replace_card_message(self, *, request_id: int, message_id: int) -> bool:
        """Move a terminal presentation reference after one replacement send."""
        result = await self._session.execute(
            update(TelegramDeliveryRequest)
            .where(TelegramDeliveryRequest.id == request_id)
            .values(card_message_id=message_id)
        )
        return _changed(result)

    async def _transition(
        self,
        *,
        request_id: int,
        user_id: int,
        expected: TelegramDeliveryStatus,
        target: TelegramDeliveryStatus,
        now: datetime,
        quality_profile: QualityProfile | None = None,
        telegram_chat_id: int | None = None,
    ) -> TelegramDeliveryRequest | None:
        values: dict[str, object] = {
            "status": target,
            "available_at": now,
            "last_error_code": None,
        }
        if quality_profile is not None:
            values["quality_profile"] = quality_profile
        conditions = [
            TelegramDeliveryRequest.id == request_id,
            TelegramDeliveryRequest.user_id == user_id,
            TelegramDeliveryRequest.status == expected,
        ]
        if telegram_chat_id is not None:
            conditions.append(TelegramDeliveryRequest.telegram_chat_id == telegram_chat_id)
        result = await self._session.execute(
            update(TelegramDeliveryRequest)
            .where(*conditions)
            .values(**values)
            .returning(TelegramDeliveryRequest)
        )
        return result.scalar_one_or_none()

    async def recover_expired(self, *, now: datetime, max_attempts: int) -> int:
        result = await self._session.execute(
            update(TelegramDeliveryRequest)
            .where(
                TelegramDeliveryRequest.status == TelegramDeliveryStatus.SENDING,
                TelegramDeliveryRequest.lease_expires_at <= now,
            )
            .values(
                status=case(
                    (
                        TelegramDeliveryRequest.attempt_count >= max_attempts,
                        TelegramDeliveryStatus.FAILED,
                    ),
                    else_=TelegramDeliveryStatus.QUEUED,
                ),
                available_at=now,
                last_error_code="DELIVERY_LEASE_EXPIRED",
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        return cast(CursorResult[Any], result).rowcount

    async def count_expired_leases(self, now: datetime) -> int:
        return int(
            await self._session.scalar(
                select(func.count(TelegramDeliveryRequest.id)).where(
                    TelegramDeliveryRequest.status == TelegramDeliveryStatus.SENDING,
                    TelegramDeliveryRequest.lease_expires_at <= now,
                )
            )
            or 0
        )

    async def claim(
        self, *, worker_id: str, now: datetime, lease_expires_at: datetime
    ) -> TelegramDeliveryRequest | None:
        terminal_subscriber = select(JobSubscriber.id).where(
            JobSubscriber.id == TelegramDeliveryRequest.subscriber_id,
            JobSubscriber.status.in_(
                (SubscriberStatus.READY, SubscriberStatus.FAILED, SubscriberStatus.CANCELLED)
            ),
        )
        candidate = (
            select(TelegramDeliveryRequest.id)
            .where(
                TelegramDeliveryRequest.available_at <= now,
                or_(
                    TelegramDeliveryRequest.status == TelegramDeliveryStatus.QUEUED,
                    and_(
                        TelegramDeliveryRequest.status == TelegramDeliveryStatus.WAITING,
                        terminal_subscriber.exists(),
                    ),
                ),
            )
            .order_by(
                TelegramDeliveryRequest.available_at,
                TelegramDeliveryRequest.created_at,
                TelegramDeliveryRequest.id,
            )
            .limit(1)
            .scalar_subquery()
        )
        result = await self._session.execute(
            update(TelegramDeliveryRequest)
            .where(
                TelegramDeliveryRequest.id == candidate,
                TelegramDeliveryRequest.status.in_(
                    (TelegramDeliveryStatus.QUEUED, TelegramDeliveryStatus.WAITING)
                ),
            )
            .values(
                status=TelegramDeliveryStatus.SENDING,
                attempt_count=TelegramDeliveryRequest.attempt_count + 1,
                lease_owner=worker_id,
                lease_expires_at=lease_expires_at,
            )
            .returning(TelegramDeliveryRequest)
        )
        return result.scalar_one_or_none()

    async def set_preparation(
        self,
        *,
        request_id: int,
        worker_id: str,
        cache_id: int | None,
        subscriber_id: str | None,
        download_job_id: int | None,
        now: datetime,
    ) -> bool:
        status = (
            TelegramDeliveryStatus.QUEUED
            if cache_id is not None
            else TelegramDeliveryStatus.WAITING
        )
        result = await self._session.execute(
            update(TelegramDeliveryRequest)
            .where(
                TelegramDeliveryRequest.id == request_id,
                TelegramDeliveryRequest.status == TelegramDeliveryStatus.SENDING,
                TelegramDeliveryRequest.lease_owner == worker_id,
            )
            .values(
                cache_id=cache_id,
                subscriber_id=subscriber_id,
                download_job_id=download_job_id,
                status=status,
                available_at=now,
                lease_owner=None,
                lease_expires_at=None,
                last_error_code=None,
            )
        )
        return _changed(result)

    async def attach_ready_cache(self, *, request_id: int, worker_id: str, cache_id: int) -> bool:
        result = await self._session.execute(
            update(TelegramDeliveryRequest)
            .where(
                TelegramDeliveryRequest.id == request_id,
                TelegramDeliveryRequest.status == TelegramDeliveryStatus.SENDING,
                TelegramDeliveryRequest.lease_owner == worker_id,
            )
            .values(cache_id=cache_id)
        )
        return _changed(result)

    async def delivered(
        self,
        *,
        request_id: int,
        worker_id: str,
        message_id: int,
        now: datetime,
    ) -> bool:
        result = await self._session.execute(
            update(TelegramDeliveryRequest)
            .where(
                TelegramDeliveryRequest.id == request_id,
                TelegramDeliveryRequest.status == TelegramDeliveryStatus.SENDING,
                TelegramDeliveryRequest.lease_owner == worker_id,
            )
            .values(
                status=TelegramDeliveryStatus.DELIVERED,
                delivered_message_id=message_id,
                delivered_at=now,
                lease_owner=None,
                lease_expires_at=None,
                last_error_code=None,
            )
        )
        return _changed(result)

    async def retry_or_fail(
        self,
        *,
        request_id: int,
        worker_id: str,
        retryable: bool,
        max_attempts: int,
        available_at: datetime,
        error_code: str,
    ) -> TelegramDeliveryStatus | None:
        row = await self.get(request_id)
        if (
            row is None
            or row.status is not TelegramDeliveryStatus.SENDING
            or row.lease_owner != worker_id
        ):
            return None
        status = (
            TelegramDeliveryStatus.QUEUED
            if retryable and row.attempt_count < max_attempts
            else TelegramDeliveryStatus.FAILED
        )
        row.status = status
        row.available_at = available_at
        row.last_error_code = error_code
        row.lease_owner = None
        row.lease_expires_at = None
        await self._session.flush()
        return status

    async def fail_terminal(
        self, *, request_id: int, worker_id: str, status: TelegramDeliveryStatus, error_code: str
    ) -> bool:
        result = await self._session.execute(
            update(TelegramDeliveryRequest)
            .where(
                TelegramDeliveryRequest.id == request_id,
                TelegramDeliveryRequest.status == TelegramDeliveryStatus.SENDING,
                TelegramDeliveryRequest.lease_owner == worker_id,
            )
            .values(
                status=status,
                last_error_code=error_code,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        return _changed(result)

    async def schedule_repair(self, *, request_id: int, worker_id: str, now: datetime) -> bool:
        result = await self._session.execute(
            update(TelegramDeliveryRequest)
            .where(
                TelegramDeliveryRequest.id == request_id,
                TelegramDeliveryRequest.status == TelegramDeliveryStatus.SENDING,
                TelegramDeliveryRequest.lease_owner == worker_id,
                TelegramDeliveryRequest.repair_count == 0,
            )
            .values(
                status=TelegramDeliveryStatus.QUEUED,
                repair_count=1,
                cache_id=None,
                subscriber_id=None,
                download_job_id=None,
                available_at=now,
                last_error_code="INVALID_CACHED_FILE",
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        return _changed(result)


def _changed(result: Any) -> bool:
    return bool(cast(CursorResult[Any], result).rowcount)
