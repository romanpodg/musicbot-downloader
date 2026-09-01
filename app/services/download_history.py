"""Projection and exact replay service for Stage 24 history."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from app.core.download import DownloadDeliveryTarget, DownloadOptions, DownloadRequest
from app.core.download_preferences import EffectiveDownloadProfile, UserDownloadPreferences
from app.core.enums import (
    BatchStatus,
    DeliveryMode,
    DownloadJobStatus,
    FormatPreference,
    MusicProviderName,
    QualityPreference,
)
from app.core.models import ResolvedCollection, ResolvedCollectionItem
from app.core.search import Album, Artist, Track
from app.services.batch_download import BatchDownloadService
from app.services.download_lifecycle import DownloadLifecycleService
from app.services.download_preferences import UserDownloadPreferencesService
from app.services.download_requests import ExistingDeliverySubmissionService
from app.storage import Database
from app.storage.models import (
    BatchDownloadRequest,
    DownloadDelivery,
    DownloadLifecycleJob,
    DownloadRequestRecord,
)


@dataclass(frozen=True, slots=True)
class TrackHistoryEntry:
    request_id: int
    provider: str | None
    provider_media_id: str | None
    title: str
    artist: str
    album: str | None
    profile: EffectiveDownloadProfile | None
    status: str
    created_at: datetime
    finished_at: datetime | None
    delivered: bool
    repeat_available: bool
    delivered_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class BatchHistoryEntry:
    batch_id: int
    title: str
    creator: str | None
    total_items: int
    succeeded_items: int
    failed_items: int
    cancelled_items: int
    status: str
    created_at: datetime
    finished_at: datetime | None
    repeat_available: bool = False


@dataclass(frozen=True, slots=True)
class HistoryPage:
    entries: tuple[TrackHistoryEntry | BatchHistoryEntry, ...]
    next_cursor: str | None


def encode_history_cursor(created_at: datetime, identifier: int) -> str:
    return f"{created_at.isoformat()}|{identifier}"


def decode_history_cursor(value: str | None) -> tuple[datetime, int] | None:
    if not value:
        return None
    try:
        timestamp, raw_id = value.split("|", 1)
        identifier = int(raw_id)
        if identifier <= 0:
            return None
        return datetime.fromisoformat(timestamp), identifier
    except (TypeError, ValueError):
        return None


class DownloadHistoryService:
    def __init__(
        self,
        database: Database,
        *,
        lifecycle: DownloadLifecycleService | None = None,
        submissions: ExistingDeliverySubmissionService | None = None,
        batch_download: BatchDownloadService | None = None,
        preferences: UserDownloadPreferencesService | None = None,
    ) -> None:
        self._database = database
        self._lifecycle = lifecycle
        self._submissions = submissions
        self._batch_download = batch_download
        self._preferences = preferences
        self._repeat_locks: dict[tuple[int, int], asyncio.Lock] = {}

    async def page(
        self, user_id: int, *, cursor: str | None = None, limit: int = 10
    ) -> HistoryPage:
        if user_id <= 0 or not 1 <= limit <= 50:
            raise ValueError("invalid history pagination")
        after = decode_history_cursor(cursor)
        if cursor is not None and after is None:
            raise ValueError("invalid history cursor")
        fetch = limit + 1
        async with self._database.transaction() as repositories:
            tracks = await repositories.download_lifecycle.list_history(
                user_id, after=after, limit=fetch
            )
            batches = await repositories.batch_download.list_history(
                user_id, after=after, limit=fetch
            )
            counts = {
                batch.id: await repositories.batch_download.counts(batch.id) for batch in batches
            }
            providers = {
                job.id: await repositories.provider_resolution.successful_provider(job.id)
                for _, job, _ in tracks
            }
        entries: list[tuple[datetime, int, TrackHistoryEntry | BatchHistoryEntry]] = []
        for request, job, delivery in tracks:
            entries.append(
                (
                    request.created_at,
                    request.id,
                    self._track_entry(request, job, delivery, provider=providers.get(job.id)),
                )
            )
        for batch in batches:
            count = counts[batch.id]
            entries.append(
                (
                    batch.created_at,
                    batch.id,
                    BatchHistoryEntry(
                        batch.id,
                        batch.title,
                        batch.creator,
                        batch.total_items,
                        count.get("SUCCEEDED", 0),
                        count.get("FAILED", 0),
                        count.get("CANCELLED", 0) + count.get("SKIPPED", 0),
                        batch.status.value,
                        batch.created_at,
                        batch.finished_at,
                        batch.status in {BatchStatus.COMPLETED, BatchStatus.PARTIAL},
                    ),
                )
            )
        entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
        selected = entries[:limit]
        next_cursor = (
            encode_history_cursor(selected[-1][0], selected[-1][1])
            if len(entries) > limit and selected
            else None
        )
        return HistoryPage(tuple(item[2] for item in selected), next_cursor)

    async def track(self, user_id: int, request_id: int) -> TrackHistoryEntry | None:
        async with self._database.transaction() as repositories:
            row = await repositories.download_lifecycle.get_request(request_id)
            if row is None or row.requester_user_id != user_id:
                return None
            job = await repositories.download_lifecycle.get_job_for_request(request_id)
            delivery = await repositories.download_lifecycle.get_delivery(job.id) if job else None
            provider = (
                await repositories.provider_resolution.successful_provider(job.id)
                if job is not None
                else None
            )
            if job is None or delivery is None or delivery.status.value != "DELIVERED":
                return None
            return self._track_entry(row, job, delivery, provider=provider)

    async def batch(self, user_id: int, batch_id: int) -> BatchHistoryEntry | None:
        async with self._database.transaction() as repositories:
            batch = await repositories.batch_download.get(batch_id)
            if batch is None or batch.requester_user_id != user_id:
                return None
            count = await repositories.batch_download.counts(batch.id)
            return BatchHistoryEntry(
                batch.id,
                batch.title,
                batch.creator,
                batch.total_items,
                count.get("SUCCEEDED", 0),
                count.get("FAILED", 0),
                count.get("CANCELLED", 0) + count.get("SKIPPED", 0),
                batch.status.value,
                batch.created_at,
                batch.finished_at,
                batch.status in {BatchStatus.COMPLETED, BatchStatus.PARTIAL},
            )

    async def repeat_batch(
        self, user_id: int, batch_id: int, *, target: DownloadDeliveryTarget
    ) -> BatchDownloadRequest | None:
        """Create a new parent batch from the immutable historical snapshot."""
        if self._batch_download is None or target.user_id != user_id:
            return None
        if target.source_message_id <= 0:
            return None
        async with self._database.transaction() as repositories:
            batch = await repositories.batch_download.get(batch_id)
            if (
                batch is None
                or batch.requester_user_id != user_id
                or batch.status not in {BatchStatus.COMPLETED, BatchStatus.PARTIAL}
            ):
                return None
            items = await repositories.batch_download.list_items(batch.id)
        if not items:
            return None
        collection = ResolvedCollection(
            source_type=batch.source_type,
            provider=batch.provider,
            collection_id=batch.source_collection_id,
            source_reference=batch.source_reference,
            title=batch.title,
            creator=batch.creator,
            items=tuple(
                ResolvedCollectionItem(
                    position=item.position,
                    provider_media_id=item.provider_media_id,
                    title=item.title,
                    artist=item.artist,
                    duration_ms=item.duration_ms,
                    source_reference=item.source_reference,
                )
                for item in items
            ),
        )
        preferences = (
            await self._preferences.get_for_user(user_id)
            if self._preferences is not None
            else _batch_preferences(batch, user_id)
        )
        return await self._batch_download.create_from_collection(
            user_id=user_id,
            confirmation_id=f"repeat-batch:{batch.id}:{target.source_message_id}",
            collection=collection,
            preferences=preferences,
        )

    async def repeat(
        self, user_id: int, request_id: int, *, target: DownloadDeliveryTarget
    ) -> object | None:
        if self._submissions is None or target.user_id != user_id:
            return None
        lock = self._repeat_locks.setdefault((user_id, request_id), asyncio.Lock())
        async with lock:
            async with self._database.transaction() as repositories:
                original = await repositories.download_lifecycle.get_request(request_id)
            if original is None or original.requester_user_id != user_id:
                return None
            async with self._database.transaction() as repositories:
                job = await repositories.download_lifecycle.get_job_for_request(request_id)
                delivery = (
                    await repositories.download_lifecycle.get_delivery(job.id) if job else None
                )
            if (
                job is None
                or delivery is None
                or job.status is not DownloadJobStatus.SUCCEEDED
                or delivery.status.value != "DELIVERED"
            ):
                return None
            if original.provider is None or original.provider_media_id is None:
                return None
            provider_value = str(original.provider)
            provider = MusicProviderName(provider_value)
            track = Track(
                id=f"history-{original.id}",
                title=original.media_title or original.provider_media_id,
                artists=(Artist(original.media_artist or "Unknown"),),
                provider=provider,
                provider_track_id=original.provider_media_id,
                album=Album(original.media_album) if original.media_album else None,
            )
            request = DownloadRequest(
                user_id=user_id,
                recognized_track=track,
                options=DownloadOptions(),
                confirmation_id=f"replay:{original.id}:{target.source_message_id}",
                replay_of_request_id=original.id,
            )
            return await self._submissions.submit(
                request, canonical_track_id=int(original.source_reference), target=target
            )

    @staticmethod
    def _track_entry(
        request: DownloadRequestRecord,
        job: DownloadLifecycleJob,
        delivery: DownloadDelivery | None,
        *,
        provider: str | None = None,
    ) -> TrackHistoryEntry:
        return TrackHistoryEntry(
            request.id,
            provider or (str(request.provider) if request.provider is not None else None),
            request.provider_media_id,
            request.media_title or request.provider_media_id or "Unknown",
            request.media_artist or "Unknown",
            request.media_album,
            request.effective_profile,
            job.status.value,
            request.created_at,
            job.finished_at,
            bool(delivery and delivery.status.value == "DELIVERED"),
            bool(delivery and delivery.status.value == "DELIVERED")
            and request.provider_media_id is not None
            and request.source_reference.isdigit(),
            delivery.delivered_at if delivery is not None else None,
        )


def _batch_preferences(batch: BatchDownloadRequest, user_id: int) -> UserDownloadPreferences:
    """Reconstruct only the persisted request preferences for legacy callers."""
    return UserDownloadPreferences(
        user_id=user_id,
        quality=getattr(batch, "requested_quality", None) or QualityPreference.BEST_AVAILABLE,
        format=getattr(batch, "requested_format", None) or FormatPreference.ORIGINAL,
        delivery_mode=getattr(batch, "delivery_mode", None) or DeliveryMode.AUDIO,
        embed_metadata=(True if batch.embed_metadata is None else batch.embed_metadata),
        embed_cover=True if batch.embed_cover is None else batch.embed_cover,
    )
