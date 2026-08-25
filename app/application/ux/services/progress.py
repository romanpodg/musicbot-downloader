"""Progress-state contract for future long-running UX operations."""

from __future__ import annotations

from dataclasses import dataclass

from app.application.ux.services.state import UserUxStateService, UxState
from app.core.enums import QueueJobStatus, TelegramDeliveryStatus


@dataclass(frozen=True, slots=True)
class UxProgress:
    user_id: int
    state: UxState


class UxProgressService:
    def __init__(self, states: UserUxStateService) -> None:
        self._states = states

    def update(self, *, user_id: int, state: UxState) -> UxProgress:
        return UxProgress(user_id=user_id, state=self._states.transition(user_id, state))


class DownloadProgressService:
    """Translate existing durable delivery/queue snapshots without inventing progress."""

    def state_for(
        self,
        *,
        delivery_status: TelegramDeliveryStatus,
        download_status: QueueJobStatus | None = None,
    ) -> UxState:
        if delivery_status is TelegramDeliveryStatus.DELIVERED:
            return UxState.DOWNLOAD_COMPLETED
        if delivery_status in {TelegramDeliveryStatus.FAILED, TelegramDeliveryStatus.CANCELLED}:
            return UxState.DOWNLOAD_FAILED
        if download_status is QueueJobStatus.FAILED or download_status is QueueJobStatus.CANCELLED:
            return UxState.DOWNLOAD_FAILED
        if delivery_status is TelegramDeliveryStatus.SENDING:
            return UxState.DOWNLOAD_PROCESSING
        if download_status is QueueJobStatus.RUNNING:
            return UxState.DOWNLOAD_PROCESSING
        return UxState.DOWNLOAD_QUEUED
