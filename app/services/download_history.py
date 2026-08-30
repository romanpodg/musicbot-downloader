"""Projection and exact replay service for Stage 24 history."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from app.core.download import DownloadDeliveryTarget, DownloadOptions, DownloadRequest
from app.core.download_preferences import EffectiveDownloadProfile
from app.core.enums import DownloadJobStatus, MusicProviderName
from app.core.search import Album, Artist, Track
from app.services.download_lifecycle import DownloadLifecycleService
from app.services.download_requests import ExistingDeliverySubmissionService
from app.storage import Database
from app.storage.models import (
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
    ) -> None:
        self._database = database
        self._lifecycle = lifecycle
        self._submissions = submissions
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
        entries: list[tuple[datetime, int, TrackHistoryEntry | BatchHistoryEntry]] = []
        for request, job, delivery in tracks:
            entries.append(
                (request.created_at, request.id, self._track_entry(request, job, delivery))
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
            return self._track_entry(row, job, delivery) if job else None

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
            if job is None or delivery is None or job.status is not DownloadJobStatus.SUCCEEDED:
                return None
            if (
                original.provider is None
                or original.provider_media_id is None
                or original.effective_profile is None
            ):
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
                effective_profile=original.effective_profile,
                replay_of_request_id=original.id,
            )
            return await self._submissions.submit(
                request, canonical_track_id=int(original.source_reference), target=target
            )

    @staticmethod
    def _track_entry(
        request: DownloadRequestRecord, job: DownloadLifecycleJob, delivery: DownloadDelivery | None
    ) -> TrackHistoryEntry:
        return TrackHistoryEntry(
            request.id,
            str(request.provider) if request.provider is not None else None,
            request.provider_media_id,
            request.media_title or request.provider_media_id or "Unknown",
            request.media_artist or "Unknown",
            request.media_album,
            request.effective_profile,
            job.status.value,
            request.created_at,
            job.finished_at,
            bool(delivery and delivery.status.value == "DELIVERED"),
            job.status is DownloadJobStatus.SUCCEEDED
            and request.provider_media_id is not None
            and request.effective_profile is not None,
        )
