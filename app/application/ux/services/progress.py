"""Progress-state contract for future long-running UX operations."""

from __future__ import annotations

from dataclasses import dataclass

from app.application.ux.services.state import UserUxStateService, UxState
from app.core.enums import QueueJobStatus, TelegramDeliveryStatus
from app.core.telegram_context import TelegramChatType, TelegramContext


@dataclass(frozen=True, slots=True)
class UxProgress:
    context: TelegramContext
    state: UxState

    @property
    def user_id(self) -> int:
        return self.context.user_id


class UxProgressService:
    def __init__(self, states: UserUxStateService) -> None:
        self._states = states

    def update(
        self,
        *,
        state: UxState,
        context: TelegramContext | None = None,
        user_id: int | None = None,
    ) -> UxProgress:
        if context is None:
            if user_id is None:
                raise ValueError("UX progress requires a Telegram context")
            context = TelegramContext(user_id, user_id, TelegramChatType.PRIVATE)
        elif user_id is not None and context.user_id != user_id:
            raise ValueError("UX progress context and user differ")
        return UxProgress(context=context, state=self._states.transition(context, state))


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
