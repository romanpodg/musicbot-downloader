"""Atomic persistence operations for Stage 23 batches."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import BatchItemStatus, BatchStatus
from app.core.models import ResolvedCollection
from app.storage.models.batch_download import BatchDownloadItem, BatchDownloadRequest
from app.storage.models.download_lifecycle import DownloadLifecycleJob


class BatchDownloadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, batch_id: int) -> BatchDownloadRequest | None:
        return await self._session.get(BatchDownloadRequest, batch_id)

    async def list_history(
        self, user_id: int, *, after: tuple[datetime, int] | None, limit: int
    ) -> list[BatchDownloadRequest]:
        statement = select(BatchDownloadRequest).where(
            BatchDownloadRequest.requester_user_id == user_id,
            BatchDownloadRequest.parent_batch_id.is_(None),
            BatchDownloadRequest.status.in_((BatchStatus.COMPLETED, BatchStatus.PARTIAL)),
        )
        if after is not None:
            created_at, batch_id = after
            statement = statement.where(
                (BatchDownloadRequest.created_at < created_at)
                | (
                    (BatchDownloadRequest.created_at == created_at)
                    & (BatchDownloadRequest.id < batch_id)
                )
            )
        rows = await self._session.scalars(
            statement.order_by(
                BatchDownloadRequest.created_at.desc(), BatchDownloadRequest.id.desc()
            ).limit(limit)
        )
        return list(rows)

    async def get_by_confirmation(self, confirmation_id: str) -> BatchDownloadRequest | None:
        return cast(
            BatchDownloadRequest | None,
            await self._session.scalar(
                select(BatchDownloadRequest).where(
                    BatchDownloadRequest.confirmation_id == confirmation_id
                )
            ),
        )

    async def record_parent_message(
        self, *, batch_id: int, user_id: int, bot_id: int, chat_id: int, message_id: int
    ) -> bool:
        result = await self._session.execute(
            update(BatchDownloadRequest)
            .where(
                BatchDownloadRequest.id == batch_id,
                BatchDownloadRequest.requester_user_id == user_id,
                BatchDownloadRequest.parent_message_id.is_(None),
            )
            .values(
                telegram_bot_id=bot_id,
                telegram_chat_id=chat_id,
                parent_message_id=message_id,
            )
        )
        return bool(cast(CursorResult[Any], result).rowcount)

    async def active_count(self, user_id: int) -> int:
        return int(
            (
                await self._session.scalar(
                    select(func.count())
                    .select_from(BatchDownloadRequest)
                    .where(
                        BatchDownloadRequest.requester_user_id == user_id,
                        BatchDownloadRequest.status.in_(
                            (BatchStatus.PENDING, BatchStatus.EXPANDING, BatchStatus.ACTIVE)
                        ),
                    )
                )
            )
            or 0
        )

    async def list_reconcilable(self) -> tuple[BatchDownloadRequest, ...]:
        result = await self._session.scalars(
            select(BatchDownloadRequest)
            .where(
                BatchDownloadRequest.status.in_(
                    (BatchStatus.PENDING, BatchStatus.EXPANDING, BatchStatus.ACTIVE)
                )
            )
            .order_by(BatchDownloadRequest.id)
        )
        return tuple(result.all())

    async def create_snapshot(
        self,
        *,
        user_id: int,
        confirmation_id: str,
        collection: ResolvedCollection,
        requested: object,
        now: datetime,
        parent_batch_id: int | None = None,
        retry_generation: int = 0,
    ) -> BatchDownloadRequest:
        """Persist metadata and every member in one transaction."""
        existing = await self.get_by_confirmation(confirmation_id)
        if existing is not None:
            return existing
        values = {
            "requester_user_id": user_id,
            "confirmation_id": confirmation_id,
            "source_type": collection.source_type,
            "provider": collection.provider,
            "source_collection_id": collection.collection_id,
            "source_reference": collection.source_reference,
            "title": collection.title,
            "creator": collection.creator,
            "status": BatchStatus.ACTIVE,
            "total_items": len(collection.items),
            "parent_batch_id": parent_batch_id,
            "retry_generation": retry_generation,
            "created_at": now,
            "updated_at": now,
            "requested_quality": getattr(requested, "quality", None),
            "requested_format": getattr(requested, "format", None),
            "delivery_mode": getattr(requested, "delivery_mode", None),
            "embed_metadata": getattr(requested, "embed_metadata", None),
            "embed_cover": getattr(requested, "embed_cover", None),
        }
        result = await self._session.execute(
            sqlite_insert(BatchDownloadRequest)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["confirmation_id"])
        )
        batch = await self.get_by_confirmation(confirmation_id)
        if batch is None:
            raise RuntimeError("batch admission failed")
        if not getattr(result, "rowcount", 0):
            return batch
        await self._session.execute(
            sqlite_insert(BatchDownloadItem),
            [
                {
                    "batch_id": batch.id,
                    "position": item.position,
                    "provider_media_id": item.provider_media_id,
                    "source_reference": item.source_reference,
                    "title": item.title,
                    "artist": item.artist,
                    "duration_ms": item.duration_ms,
                    "status": BatchItemStatus.PENDING,
                    "created_at": now,
                    "updated_at": now,
                }
                for item in collection.items
            ],
        )
        return batch

    async def list_items(self, batch_id: int) -> tuple[BatchDownloadItem, ...]:
        result = await self._session.scalars(
            select(BatchDownloadItem)
            .where(BatchDownloadItem.batch_id == batch_id)
            .order_by(BatchDownloadItem.position)
        )
        return tuple(result.all())

    async def set_item(
        self,
        item_id: int,
        *,
        status: BatchItemStatus,
        now: datetime,
        download_request_id: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        values: dict[str, object] = {
            "status": status,
            "updated_at": now,
            "error_code": error_code,
            "error_message": error_message,
        }
        if download_request_id is not None:
            values["download_request_id"] = download_request_id
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(BatchDownloadItem).where(BatchDownloadItem.id == item_id).values(**values)
            ),
        )
        return bool(result.rowcount)

    async def claim_pending_item(self, item_id: int, now: datetime) -> bool:
        """Atomically reserve one item for admission.

        The reservation uses the existing ADMITTED presentation state; an
        orphaned reservation is returned to PENDING by reconciliation.
        """
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(BatchDownloadItem)
                .where(
                    BatchDownloadItem.id == item_id,
                    BatchDownloadItem.status == BatchItemStatus.PENDING,
                    BatchDownloadItem.download_request_id.is_(None),
                    BatchDownloadRequest.id == BatchDownloadItem.batch_id,
                    BatchDownloadRequest.cancel_requested_at.is_(None),
                )
                .values(status=BatchItemStatus.ADMITTED, updated_at=now)
            ),
        )
        return bool(result.rowcount)

    async def recover_orphaned_admission(self, batch_id: int, now: datetime) -> int:
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(BatchDownloadItem)
                .where(
                    BatchDownloadItem.batch_id == batch_id,
                    BatchDownloadItem.status == BatchItemStatus.ADMITTED,
                    BatchDownloadItem.download_request_id.is_(None),
                )
                .values(status=BatchItemStatus.PENDING, updated_at=now)
            ),
        )
        return int(result.rowcount or 0)

    async def cancel_pending(self, batch_id: int, now: datetime) -> int:
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(BatchDownloadItem)
                .where(
                    BatchDownloadItem.batch_id == batch_id,
                    BatchDownloadItem.status == BatchItemStatus.PENDING,
                )
                .values(
                    status=BatchItemStatus.SKIPPED,
                    error_code="BATCH_CANCELLED",
                    error_message="cancelled before admission",
                    updated_at=now,
                )
            ),
        )
        return int(result.rowcount or 0)

    async def request_cancel(self, batch_id: int, now: datetime) -> bool:
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(BatchDownloadRequest)
                .where(
                    BatchDownloadRequest.id == batch_id,
                    BatchDownloadRequest.cancel_requested_at.is_(None),
                    BatchDownloadRequest.status.not_in(
                        (
                            BatchStatus.COMPLETED,
                            BatchStatus.PARTIAL,
                            BatchStatus.FAILED,
                            BatchStatus.CANCELLED,
                        )
                    ),
                )
                .values(cancel_requested_at=now, updated_at=now)
            ),
        )
        return bool(result.rowcount)

    async def mark_status(self, batch_id: int, status: BatchStatus, now: datetime) -> bool:
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(BatchDownloadRequest)
                .where(
                    BatchDownloadRequest.id == batch_id,
                    BatchDownloadRequest.status.not_in(
                        (
                            BatchStatus.COMPLETED,
                            BatchStatus.PARTIAL,
                            BatchStatus.FAILED,
                            BatchStatus.CANCELLED,
                        )
                    ),
                )
                .values(
                    status=status,
                    finished_at=(
                        now
                        if status
                        in {
                            BatchStatus.COMPLETED,
                            BatchStatus.PARTIAL,
                            BatchStatus.FAILED,
                            BatchStatus.CANCELLED,
                        }
                        else None
                    ),
                    updated_at=now,
                )
            ),
        )
        return bool(result.rowcount)

    async def counts(self, batch_id: int) -> Counter[str]:
        items = await self.list_items(batch_id)
        counts: Counter[str] = Counter()
        for item in items:
            if item.download_request_id is not None:
                status = await self._session.scalar(
                    select(DownloadLifecycleJob.status).where(
                        DownloadLifecycleJob.request_id == item.download_request_id
                    )
                )
                if status is None:
                    counts[item.status.value] += 1
                else:
                    counts[str(status.value if hasattr(status, "value") else status)] += 1
            else:
                counts[item.status.value] += 1
        for key in (
            "PENDING",
            "QUEUED",
            "RUNNING",
            "RETRY_WAIT",
            "DELIVERING",
            "SUCCEEDED",
            "FAILED",
            "CANCELLED",
            "SKIPPED",
        ):
            counts.setdefault(key, 0)
        return counts
