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

    async def get_by_confirmation(self, confirmation_id: str) -> BatchDownloadRequest | None:
        return cast(
            BatchDownloadRequest | None,
            await self._session.scalar(
                select(BatchDownloadRequest).where(
                    BatchDownloadRequest.confirmation_id == confirmation_id
                )
            ),
        )

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
        await self._session.execute(sqlite_insert(BatchDownloadRequest).values(**values))
        batch = await self.get_by_confirmation(confirmation_id)
        if batch is None:
            raise RuntimeError("batch admission failed")
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

    async def request_cancel(self, batch_id: int, now: datetime) -> bool:
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(BatchDownloadRequest)
                .where(
                    BatchDownloadRequest.id == batch_id,
                    BatchDownloadRequest.cancel_requested_at.is_(None),
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
        return counts
