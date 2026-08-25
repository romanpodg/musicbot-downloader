"""Stage 18 adapter to the existing durable Telegram delivery admission path."""

from __future__ import annotations

from app.core.download import (
    DownloadDeliveryTarget,
    DownloadRequest,
    DownloadSubmission,
    DownloadSubmissionState,
)
from app.core.enums import TelegramDeliveryStatus
from app.services.telegram_requests import TelegramTrackRequestService
from app.storage import Database
from app.storage.models import TelegramDeliveryRequest


class ExistingDeliverySubmissionService:
    """Uses Stage 9's request/outbox lifecycle; it never owns a queue or downloader."""

    def __init__(self, database: Database, requests: TelegramTrackRequestService) -> None:
        self._database = database
        self._requests = requests

    async def submit(
        self,
        request: DownloadRequest,
        *,
        canonical_track_id: int,
        target: DownloadDeliveryTarget,
    ) -> DownloadSubmission:
        if request.user_id != target.user_id:
            raise ValueError("download request and delivery target users differ")
        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(target.user_id)
        if user is None:
            raise ValueError("download user is not available for delivery")

        delivery = await self._requests.request_track_id(
            user=user,
            telegram_chat_id=target.destination_id,
            source_message_id=target.source_message_id,
            track_id=canonical_track_id,
        )

        if request.options.quality_profile is not None:
            delivery = await self._apply_requested_quality(delivery.id, request, target.user_id)
        elif delivery.status is TelegramDeliveryStatus.AWAITING_ACTION:
            started = await self._requests.start_default_quality(
                request_id=delivery.id, telegram_user_id=target.user_id
            )
            if started.accepted and started.request is not None:
                delivery = started.request

        return DownloadSubmission(
            request=request,
            canonical_track_id=canonical_track_id,
            delivery_request_id=delivery.id,
            state=_submission_state(delivery.status),
            download_job_id=delivery.download_job_id,
        )

    async def _apply_requested_quality(
        self, delivery_request_id: int, request: DownloadRequest, user_id: int
    ) -> TelegramDeliveryRequest:
        assert request.options.quality_profile is not None
        card = await self._requests.track_card(
            request_id=delivery_request_id, telegram_user_id=user_id
        )
        if card is None:
            raise ValueError("delivery request is no longer available")
        if card.status is TelegramDeliveryStatus.AWAITING_QUALITY:
            first_quality = await self._requests.choose_first_quality(
                request_id=delivery_request_id,
                telegram_user_id=user_id,
                quality_profile=request.options.quality_profile,
            )
            if first_quality.accepted and first_quality.request is not None:
                return first_quality.request
        elif card.status is TelegramDeliveryStatus.AWAITING_ACTION:
            opened = await self._requests.open_track_quality(
                request_id=delivery_request_id, telegram_user_id=user_id
            )
            if opened.accepted:
                selected_quality = await self._requests.choose_track_quality(
                    request_id=delivery_request_id,
                    telegram_user_id=user_id,
                    quality_profile=request.options.quality_profile,
                )
                if selected_quality.accepted and selected_quality.request is not None:
                    return selected_quality.request
        raise ValueError("delivery request cannot accept the requested quality")


def _submission_state(status: TelegramDeliveryStatus) -> DownloadSubmissionState:
    if status is TelegramDeliveryStatus.AWAITING_QUALITY:
        return DownloadSubmissionState.AWAITING_QUALITY
    if status in {TelegramDeliveryStatus.DELIVERED}:
        return DownloadSubmissionState.COMPLETED
    if status in {TelegramDeliveryStatus.FAILED, TelegramDeliveryStatus.CANCELLED}:
        return DownloadSubmissionState.FAILED
    if status in {TelegramDeliveryStatus.WAITING, TelegramDeliveryStatus.SENDING}:
        return DownloadSubmissionState.PROCESSING
    return DownloadSubmissionState.QUEUED
