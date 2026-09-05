"""Best-effort Stage 28 parent presentation over authoritative Stage 23 aggregates."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from app.core.enums import BatchStatus
from app.services.batch_download import BatchDownloadService, BatchProgress
from app.storage import Database
from app.storage.models.base import utc_now
from app.telegram import TelegramGateway, TelegramGatewayError
from app.telegram.ux_presentation import (
    DownloadStatusView,
    TelegramStatusUpdatePolicy,
    UserDownloadState,
)

logger = logging.getLogger(__name__)


class CollectionStatusPresentationService:
    def __init__(
        self,
        database: Database,
        batches: BatchDownloadService,
        gateway: TelegramGateway,
        *,
        update_policy: TelegramStatusUpdatePolicy | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._database = database
        self._batches = batches
        self._gateway = gateway
        self._update_policy = update_policy or TelegramStatusUpdatePolicy(clock=clock)

    async def refresh(self, batch_id: int) -> None:
        async with self._database.transaction() as repositories:
            batch = await repositories.batch_download.get(batch_id)
            if batch is None or batch.parent_message_id is None or batch.telegram_chat_id is None:
                return
        progress = await self._batches.progress(batch_id)
        if progress is None:
            return
        text = _text(batch.title, batch.source_type.value, batch.status, progress)
        view = _status_view(batch.status, text)
        if not self._update_policy.should_emit(
            batch.telegram_chat_id,
            batch.parent_message_id,
            view,
            key=("collection", batch_id),
        ):
            return
        try:
            await self._gateway.edit_text(batch.telegram_chat_id, batch.parent_message_id, text)
        except TelegramGatewayError as exc:
            if not view.terminal or exc.retryable:
                return
            try:
                replacement = await self._gateway.send_text(batch.telegram_chat_id, text)
                async with self._database.transaction() as repositories:
                    await repositories.batch_download.replace_parent_message(
                        batch_id=batch_id, message_id=replacement.message_id
                    )
            except TelegramGatewayError:
                logger.info("Collection status presentation failed", extra={"batch_id": batch_id})

    async def reconcile_startup(self, *, limit: int = 50) -> int:
        async with self._database.transaction() as repositories:
            ids = await repositories.batch_download.list_parent_presentation_candidates(limit=limit)
        for batch_id in ids:
            try:
                await self.refresh(batch_id)
            except Exception:
                logger.info("Collection startup presentation failed", extra={"batch_id": batch_id})
        return len(ids)


def _text(title: str, source_type: str, status: BatchStatus, progress: BatchProgress) -> str:
    noun = "Playlist" if source_type == "playlist" else "Album"
    if status is BatchStatus.COMPLETED:
        return f"{title}\n\n✓ {noun} delivered\n\n{progress.succeeded} / {progress.total} tracks"
    if status is BatchStatus.PARTIAL:
        unavailable = progress.failed + progress.cancelled + progress.skipped
        return (
            f"{title}\n\n{noun} completed with unavailable tracks\n\n"
            f"✓ {progress.succeeded} delivered\n✗ {unavailable} unavailable"
        )
    if status is BatchStatus.CANCELLED:
        return f"{title}\n\n{noun} download cancelled."
    if status is BatchStatus.FAILED:
        return f"{title}\n\n{noun} could not be completed."
    active = progress.running + progress.delivering
    waiting = progress.pending + progress.queued + progress.retry_wait
    unavailable = progress.failed + progress.cancelled + progress.skipped
    unavailable_line = f"\n{unavailable} unavailable" if unavailable else ""
    return (
        f"{title}\n\nDownloading {source_type}\n\n"
        f"{progress.succeeded} / {progress.total} delivered\n{active} active\n{waiting} waiting"
        f"{unavailable_line}"
    )


def _status_view(status: BatchStatus, text: str) -> DownloadStatusView:
    """Adapt the batch aggregate to the shared edit policy vocabulary."""
    if status is BatchStatus.COMPLETED:
        return DownloadStatusView(UserDownloadState.DELIVERED, text, True)
    if status is BatchStatus.CANCELLED:
        return DownloadStatusView(UserDownloadState.CANCELLED, text, True)
    if status in {BatchStatus.PARTIAL, BatchStatus.FAILED}:
        return DownloadStatusView(UserDownloadState.FAILED, text, True)
    if status in {BatchStatus.PENDING, BatchStatus.EXPANDING}:
        return DownloadStatusView(UserDownloadState.PREPARING, text, False)
    return DownloadStatusView(UserDownloadState.DOWNLOADING, text, False)
