"""Stage 23 collection snapshot and aggregation orchestration.

This service deliberately delegates every child to a caller supplied Stage 21
admission port; it never downloads media or owns a second queue.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol, cast

from app.core.download import DownloadDeliveryTarget, DownloadOptions, DownloadRequest
from app.core.download_preferences import EffectiveDownloadProfile, UserDownloadPreferences
from app.core.enums import BatchItemStatus, BatchSourceType, BatchStatus
from app.core.models import ResolvedCollection, ResolvedCollectionItem
from app.core.search import Artist, Track
from app.storage import Database
from app.storage.models.base import utc_now


class CollectionResolver(Protocol):
    async def resolve_collection(
        self, source_type: BatchSourceType, source_reference: str
    ) -> ResolvedCollection: ...


class ChildAdmission(Protocol):
    async def __call__(
        self, request: DownloadRequest, *, target: DownloadDeliveryTarget
    ) -> object: ...


class BatchLimitExceeded(ValueError):
    pass


class ActiveBatchLimitExceeded(ValueError):
    pass


class BatchDownloadService:
    def __init__(
        self,
        database: Database,
        resolver: CollectionResolver,
        child_admitter: ChildAdmission | None = None,
        *,
        child_canceller: Callable[[int], object] | None = None,
        max_items: int = 100,
        max_active_batches_per_user: int = 2,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if max_items < 1 or max_active_batches_per_user < 1:
            raise ValueError("batch limits must be positive")
        self.database = database
        self.resolver = resolver
        self.child_admitter = child_admitter
        self.child_canceller = child_canceller
        self.max_items = max_items
        self.max_active_batches_per_user = max_active_batches_per_user
        self.clock = clock

    async def expand(
        self,
        *,
        user_id: int,
        confirmation_id: str,
        source_type: BatchSourceType,
        source_reference: str,
        preferences: UserDownloadPreferences,
        parent_batch_id: int | None = None,
        retry_generation: int = 0,
    ) -> object:
        collection = await self.resolver.resolve_collection(source_type, source_reference)
        if len(collection.items) > self.max_items:
            raise BatchLimitExceeded(
                f"collection contains {len(collection.items)} items; maximum is {self.max_items}"
            )
        async with self.database.transaction() as repositories:
            existing = await repositories.batch_download.get_by_confirmation(confirmation_id)
            if existing is not None:
                return existing
            if (
                await repositories.batch_download.active_count(user_id)
                >= self.max_active_batches_per_user
            ):
                raise ActiveBatchLimitExceeded("maximum active batches reached")
            return await repositories.batch_download.create_snapshot(
                user_id=user_id,
                confirmation_id=confirmation_id,
                collection=collection,
                requested=preferences,
                parent_batch_id=parent_batch_id,
                retry_generation=retry_generation,
                now=self.clock(),
            )

    async def admit_pending(self, batch_id: int, *, target: DownloadDeliveryTarget) -> int:
        if self.child_admitter is None:
            raise RuntimeError("child admission port is not configured")
        async with self.database.transaction() as repositories:
            batch = await repositories.batch_download.get(batch_id)
            if batch is None or batch.status in {
                BatchStatus.COMPLETED,
                BatchStatus.PARTIAL,
                BatchStatus.FAILED,
                BatchStatus.CANCELLED,
            }:
                return 0
            items = await repositories.batch_download.list_items(batch_id)
        admitted = 0
        for item in items:
            if item.status is not BatchItemStatus.PENDING:
                continue
            if batch.cancel_requested_at is not None:
                break
            artist = Artist(item.artist or batch.creator or "Unknown")
            track = Track(
                id=f"batch-{batch.id}-{item.position}",
                title=item.title or item.provider_media_id,
                artists=(artist,),
                provider=batch.provider,
                provider_track_id=item.provider_media_id,
                duration_ms=item.duration_ms,
            )
            request = DownloadRequest(
                user_id=batch.requester_user_id,
                recognized_track=track,
                options=DownloadOptions(),
                confirmation_id=f"{batch.confirmation_id}:{item.position}",
                effective_profile=self._frozen_profile(batch),
            )
            try:
                result = await self.child_admitter(request, target=target)
            except Exception as exc:
                async with self.database.transaction() as repositories:
                    await repositories.batch_download.set_item(
                        item.id,
                        status=BatchItemStatus.SKIPPED,
                        now=self.clock(),
                        error_code="MEDIA_UNAVAILABLE",
                        error_message=str(exc)[:256],
                    )
                continue
            async with self.database.transaction() as repositories:
                request_id = getattr(result, "request_id", None)
                delivery_id = getattr(result, "delivery_request_id", None)
                if request_id is None and delivery_id is not None:
                    pair = await repositories.download_lifecycle.get_by_telegram_request(
                        delivery_id
                    )
                    request_id = pair[0].request_id if pair is not None else None
                await repositories.batch_download.set_item(
                    item.id,
                    status=BatchItemStatus.ADMITTED,
                    now=self.clock(),
                    download_request_id=request_id,
                )
            admitted += 1
        return admitted

    async def cancel(self, batch_id: int, *, user_id: int) -> bool:
        async with self.database.transaction() as repositories:
            batch = await repositories.batch_download.get(batch_id)
            if batch is None or batch.requester_user_id != user_id:
                return False
            changed = await repositories.batch_download.request_cancel(batch_id, self.clock())
            items = await repositories.batch_download.list_items(batch_id)
        if self.child_canceller is not None:
            for item in items:
                if item.download_request_id is not None:
                    result = self.child_canceller(item.download_request_id)
                    if hasattr(result, "__await__"):
                        await result
        return changed

    @staticmethod
    def _frozen_profile(batch: object) -> EffectiveDownloadProfile | None:
        quality = getattr(batch, "requested_quality", None)
        fmt = getattr(batch, "requested_format", None)
        mode = getattr(batch, "delivery_mode", None)
        metadata = getattr(batch, "embed_metadata", None)
        cover = getattr(batch, "embed_cover", None)
        if None in {quality, fmt, mode, metadata, cover}:
            return None
        from app.core.enums import DeliveryMode, FormatPreference, QualityPreference

        requested_quality = cast(QualityPreference, quality)
        requested_format = cast(FormatPreference, fmt)
        delivery_mode = cast(DeliveryMode, mode)
        effective = (
            QualityPreference.HIGH
            if requested_quality is QualityPreference.BEST_AVAILABLE
            else requested_quality
        )
        return EffectiveDownloadProfile(
            requested_quality=requested_quality,
            effective_quality=effective,
            requested_format=requested_format,
            effective_format=requested_format,
            delivery_mode=delivery_mode,
            embed_metadata=cast(bool, metadata),
            embed_cover=cast(bool, cover),
        )

    async def retry_failed(self, batch_id: int, *, user_id: int) -> object | None:
        """Create one linked retry batch from unsuccessful snapshot items."""
        from app.core.enums import DeliveryMode, FormatPreference, QualityPreference

        async with self.database.transaction() as repositories:
            batch = await repositories.batch_download.get(batch_id)
            if batch is None or batch.requester_user_id != user_id:
                return None
            items = await repositories.batch_download.list_items(batch_id)
            retry_items = tuple(
                ResolvedCollectionItem(
                    position=index,
                    provider_media_id=item.provider_media_id,
                    title=item.title,
                    artist=item.artist,
                    duration_ms=item.duration_ms,
                    source_reference=item.source_reference,
                )
                for index, item in enumerate(
                    (
                        item
                        for item in items
                        if item.status in {BatchItemStatus.FAILED, BatchItemStatus.SKIPPED}
                    ),
                    start=1,
                )
            )
            if not retry_items:
                return None
            existing = await repositories.batch_download.get_by_confirmation(
                f"retry:{batch.id}:{batch.retry_generation + 1}"
            )
            if existing is not None:
                return existing
            collection = ResolvedCollection(
                source_type=batch.source_type,
                provider=batch.provider,
                collection_id=batch.source_collection_id,
                source_reference=batch.source_reference,
                title=batch.title,
                creator=batch.creator,
                items=retry_items,
            )
            preferences = UserDownloadPreferences(
                user_id=user_id,
                quality=batch.requested_quality or QualityPreference.BEST_AVAILABLE,
                format=batch.requested_format or FormatPreference.ORIGINAL,
                delivery_mode=batch.delivery_mode or DeliveryMode.AUDIO,
                embed_metadata=True if batch.embed_metadata is None else batch.embed_metadata,
                embed_cover=True if batch.embed_cover is None else batch.embed_cover,
            )
            return await repositories.batch_download.create_snapshot(
                user_id=user_id,
                confirmation_id=f"retry:{batch.id}:{batch.retry_generation + 1}",
                collection=collection,
                requested=preferences,
                parent_batch_id=batch.id,
                retry_generation=batch.retry_generation + 1,
                now=self.clock(),
            )

    async def reconcile(self, batch_id: int) -> BatchStatus | None:
        async with self.database.transaction() as repositories:
            batch = await repositories.batch_download.get(batch_id)
            if batch is None:
                return None
            counts = await repositories.batch_download.counts(batch_id)
            total = sum(counts.values())
            unfinished = (
                counts[BatchItemStatus.PENDING.value] + counts[BatchItemStatus.ADMITTED.value]
            )
            if unfinished:
                return batch.status
            succeeded = counts.get("SUCCEEDED", 0)
            if batch.cancel_requested_at is not None:
                status = BatchStatus.CANCELLED
            elif succeeded == total and total:
                status = BatchStatus.COMPLETED
            elif succeeded:
                status = BatchStatus.PARTIAL
            else:
                status = BatchStatus.FAILED
            await repositories.batch_download.mark_status(batch_id, status, self.clock())
            return status
