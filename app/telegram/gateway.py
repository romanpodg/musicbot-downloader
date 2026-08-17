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


class TelegramGatewayError(Exception):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__()
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class TelegramGateway(Protocol):
    async def get_bot_identity(self) -> TelegramBotIdentity: ...

    async def upload_audio(self, spec: TelegramUploadSpec) -> TelegramUploadReceipt: ...

    async def upload_document(self, spec: TelegramUploadSpec) -> TelegramUploadReceipt: ...

    async def close(self) -> None: ...
