"""Library-neutral Telegram gateway contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.core.models import TelegramBotIdentity, TelegramUploadReceipt


@dataclass(frozen=True, slots=True)
class TelegramUploadSpec:
    chat_id: int | str
    file_path: Path
    display_filename: str
    title: str | None = None
    performer: str | None = None
    duration_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class TelegramCachedMediaSpec:
    chat_id: int
    file_id: str


@dataclass(frozen=True, slots=True)
class TelegramDeliveryReceipt:
    chat_id: int
    message_id: int


class TelegramGatewayError(Exception):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        retry_after_seconds: float | None = None,
        invalid_cached_file: bool = False,
    ) -> None:
        super().__init__()
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.invalid_cached_file = invalid_cached_file


class TelegramGateway(Protocol):
    async def get_bot_identity(self) -> TelegramBotIdentity: ...

    async def upload_audio(self, spec: TelegramUploadSpec) -> TelegramUploadReceipt: ...

    async def upload_document(self, spec: TelegramUploadSpec) -> TelegramUploadReceipt: ...

    async def send_cached_audio(self, spec: TelegramCachedMediaSpec) -> TelegramDeliveryReceipt: ...

    async def send_cached_document(
        self, spec: TelegramCachedMediaSpec
    ) -> TelegramDeliveryReceipt: ...

    async def send_text(self, chat_id: int, text: str) -> TelegramDeliveryReceipt: ...

    async def edit_text(self, chat_id: int, message_id: int, text: str) -> None: ...

    async def can_send_messages(self, chat_id: int) -> bool: ...

    async def close(self) -> None: ...
