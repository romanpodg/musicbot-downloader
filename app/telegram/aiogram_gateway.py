"""Async aiogram implementation of the narrow Stage 8 Telegram boundary."""

from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiogram.types import FSInputFile, Message
from aiogram.utils.token import TokenValidationError

from app.core.enums import QueueErrorCode, TelegramMediaKind
from app.core.models import TelegramBotIdentity, TelegramUploadReceipt
from app.telegram.gateway import TelegramGatewayError, TelegramUploadSpec


class AiogramTelegramGateway:
    def __init__(self, token: str) -> None:
        try:
            self._bot = Bot(token=token)
        except TokenValidationError:
            raise TelegramGatewayError(
                QueueErrorCode.TELEGRAM_AUTH_FAILED.value, retryable=False
            ) from None
        self._identity: TelegramBotIdentity | None = None
        self._identity_lock = asyncio.Lock()

    async def get_bot_identity(self) -> TelegramBotIdentity:
        if self._identity is not None:
            return self._identity
        async with self._identity_lock:
            if self._identity is not None:
                return self._identity
            try:
                me = await self._bot.get_me()
            except TelegramAPIError as exc:
                raise _normalized_error(exc) from None
            self._identity = TelegramBotIdentity(me.id, me.username)
            return self._identity

    async def upload_audio(self, spec: TelegramUploadSpec) -> TelegramUploadReceipt:
        identity = await self.get_bot_identity()
        try:
            message = await self._bot.send_audio(
                chat_id=spec.chat_id,
                audio=FSInputFile(spec.file_path, filename=spec.display_filename),
                title=spec.title,
                performer=spec.performer,
                duration=spec.duration_seconds,
            )
        except TelegramAPIError as exc:
            raise _normalized_error(exc) from None
        if message.audio is None:
            raise TelegramGatewayError(
                QueueErrorCode.TELEGRAM_INVALID_RECEIPT.value, retryable=False
            )
        return _receipt(identity, message, TelegramMediaKind.AUDIO)

    async def upload_document(self, spec: TelegramUploadSpec) -> TelegramUploadReceipt:
        identity = await self.get_bot_identity()
        try:
            message = await self._bot.send_document(
                chat_id=spec.chat_id,
                document=FSInputFile(spec.file_path, filename=spec.display_filename),
            )
        except TelegramAPIError as exc:
            raise _normalized_error(exc) from None
        if message.document is None:
            raise TelegramGatewayError(
                QueueErrorCode.TELEGRAM_INVALID_RECEIPT.value, retryable=False
            )
        return _receipt(identity, message, TelegramMediaKind.DOCUMENT)

    async def close(self) -> None:
        await self._bot.session.close()


def _receipt(
    identity: TelegramBotIdentity, message: Message, kind: TelegramMediaKind
) -> TelegramUploadReceipt:
    media = message.audio if kind is TelegramMediaKind.AUDIO else message.document
    if media is None:
        raise TelegramGatewayError(QueueErrorCode.TELEGRAM_INVALID_RECEIPT.value, retryable=False)
    return TelegramUploadReceipt(
        telegram_bot_id=identity.telegram_bot_id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        media_kind=kind,
        file_id=media.file_id,
        file_unique_id=media.file_unique_id,
        file_size_bytes=media.file_size,
    )


def _normalized_error(exc: TelegramAPIError) -> TelegramGatewayError:
    if isinstance(exc, TelegramRetryAfter):
        return TelegramGatewayError(
            QueueErrorCode.TELEGRAM_RATE_LIMITED.value,
            retryable=True,
            retry_after_seconds=float(exc.retry_after),
        )
    if isinstance(exc, TelegramNetworkError):
        return TelegramGatewayError(QueueErrorCode.TELEGRAM_TRANSPORT_ERROR.value, retryable=True)
    if isinstance(exc, TelegramServerError):
        return TelegramGatewayError(QueueErrorCode.TELEGRAM_SERVER_ERROR.value, retryable=True)
    if isinstance(exc, TelegramUnauthorizedError):
        return TelegramGatewayError(QueueErrorCode.TELEGRAM_AUTH_FAILED.value, retryable=False)
    if isinstance(exc, TelegramForbiddenError):
        return TelegramGatewayError(
            QueueErrorCode.TELEGRAM_PERMISSION_DENIED.value, retryable=False
        )
    if isinstance(exc, TelegramBadRequest):
        return TelegramGatewayError(QueueErrorCode.TELEGRAM_BAD_REQUEST.value, retryable=False)
    return TelegramGatewayError(QueueErrorCode.TELEGRAM_BAD_REQUEST.value, retryable=False)
