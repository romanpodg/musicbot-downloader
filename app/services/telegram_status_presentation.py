"""Durable Stage 28 status repair over existing request message references."""

from __future__ import annotations

import logging

from app.core.enums import DownloadFailureCode
from app.storage import Database
from app.telegram import TelegramGateway, TelegramGatewayError
from app.telegram.ux_presentation import DownloadStatusPresenter

logger = logging.getLogger(__name__)


class TelegramStatusPresentationService:
    """Best-effort Telegram rendering; lifecycle correctness never depends on it."""

    def __init__(
        self,
        database: Database,
        gateway: TelegramGateway,
        *,
        presenter: DownloadStatusPresenter | None = None,
    ) -> None:
        self._database = database
        self._gateway = gateway
        self._presenter = presenter or DownloadStatusPresenter()

    async def reconcile_delivery(self, telegram_request_id: int) -> None:
        async with self._database.transaction() as repositories:
            pair = await repositories.download_lifecycle.get_by_telegram_request(
                telegram_request_id
            )
            delivery_request = await repositories.telegram_delivery.get(telegram_request_id)
            if pair is None or delivery_request is None or delivery_request.card_message_id is None:
                return
            job, _ = pair
            request = await repositories.download_lifecycle.get_request(job.request_id)
            if request is None:
                return
            view = self._presenter.present(job.status, job.phase, _failure_code(job.error_code))
            title = request.media_title or "Track"
            artist = f"{request.media_artist} — " if request.media_artist else ""
            text = f"{artist}{title}\n\n{view.label}"
            chat_id = delivery_request.telegram_chat_id
            message_id = delivery_request.card_message_id
        try:
            await self._gateway.edit_text(chat_id, message_id, text)
        except TelegramGatewayError:
            if not view.terminal:
                return
            try:
                replacement = await self._gateway.send_text(chat_id, text)
                async with self._database.transaction() as repositories:
                    await repositories.telegram_delivery.replace_card_message(
                        request_id=telegram_request_id, message_id=replacement.message_id
                    )
            except TelegramGatewayError:
                logger.info(
                    "Telegram terminal status replacement failed",
                    extra={"request_id": telegram_request_id},
                )

    async def reconcile_startup(self, *, limit: int = 50) -> int:
        """Best-effort bounded repair after durable recovery has completed."""
        async with self._database.transaction() as repositories:
            candidates = await repositories.telegram_delivery.list_status_presentation_candidates(
                limit=limit
            )
        repaired = 0
        for request_id in candidates:
            try:
                await self.reconcile_delivery(request_id)
                repaired += 1
            except Exception:
                logger.info(
                    "Telegram startup status reconciliation failed",
                    extra={"request_id": request_id},
                )
        return repaired


def _failure_code(value: str | None) -> DownloadFailureCode | None:
    try:
        return DownloadFailureCode(value) if value is not None else None
    except ValueError:
        return None
