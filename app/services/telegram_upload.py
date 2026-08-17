"""Production generic UploadExecutor backed by the Telegram completed cache."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import NoReturn

from app.core.enums import NativeContainer, QueueErrorCode, TelegramMediaKind
from app.core.exceptions import (
    DatabaseError,
    UploadRetryableError,
    UploadTerminalError,
)
from app.core.models import UploadRequest, UploadResult
from app.services.telegram_cache import TelegramFileCacheService
from app.storage import Database
from app.telegram import TelegramGateway, TelegramGatewayError, TelegramUploadSpec

MAX_DISPLAY_FILENAME_LENGTH = 180
MAX_TELEGRAM_TEXT_LENGTH = 512
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class TelegramCacheUploadExecutor:
    def __init__(
        self,
        database: Database,
        cache: TelegramFileCacheService,
        gateway: TelegramGateway,
        *,
        cache_chat_id: int | str,
    ) -> None:
        self._database = database
        self._cache = cache
        self._gateway = gateway
        self._cache_chat_id = cache_chat_id

    async def upload(self, request: UploadRequest) -> UploadResult:
        artifact = request.artifact
        if artifact is None:
            raise UploadTerminalError(QueueErrorCode.TELEGRAM_CACHE_METADATA_MISSING.value)
        try:
            if request.artifact_path.stat().st_size != artifact.file_size_bytes:
                raise UploadTerminalError(QueueErrorCode.UPLOAD_ARTIFACT_INVALID.value)
        except OSError as exc:
            raise UploadTerminalError(QueueErrorCode.UPLOAD_ARTIFACT_MISSING.value) from exc

        try:
            identity = await self._gateway.get_bot_identity()
        except TelegramGatewayError as exc:
            _raise_upload_error(exc)

        existing = await self._cache.get_active(
            telegram_bot_id=identity.telegram_bot_id,
            track_id=request.track_id,
            quality_profile=request.quality_profile,
        )
        if existing is not None:
            return UploadResult(external_id=str(existing.cache_id))

        async with self._database.transaction() as repositories:
            track = await repositories.tracks.get_track_by_id(request.track_id)
        if track is None:
            raise UploadTerminalError(QueueErrorCode.TELEGRAM_CACHE_METADATA_MISSING.value)

        media_kind = telegram_media_kind(artifact.output_container)
        extension = _extension(artifact.output_container, request.artifact_path)
        filename = sanitize_display_filename(track.artist, track.title, extension)
        spec = TelegramUploadSpec(
            chat_id=self._cache_chat_id,
            file_path=request.artifact_path,
            display_filename=filename,
            title=_telegram_text(track.title),
            performer=_telegram_text(track.artist),
            duration_seconds=(
                max(1, round(artifact.duration_ms / 1000))
                if artifact.duration_ms is not None
                else None
            ),
        )
        try:
            receipt = (
                await self._gateway.upload_audio(spec)
                if media_kind is TelegramMediaKind.AUDIO
                else await self._gateway.upload_document(spec)
            )
        except TelegramGatewayError as exc:
            _raise_upload_error(exc)

        if (
            receipt.telegram_bot_id != identity.telegram_bot_id
            or receipt.media_kind is not media_kind
        ):
            raise UploadTerminalError(QueueErrorCode.TELEGRAM_INVALID_RECEIPT.value)
        try:
            cached = await self._cache.upsert_success(
                track_id=request.track_id,
                quality_profile=request.quality_profile,
                receipt=receipt,
                artifact=artifact,
            )
        except (DatabaseError, RuntimeError, ValueError) as exc:
            winner = await self._cache.get_active(
                telegram_bot_id=identity.telegram_bot_id,
                track_id=request.track_id,
                quality_profile=request.quality_profile,
            )
            if winner is not None:
                return UploadResult(external_id=str(winner.cache_id))
            raise UploadRetryableError(
                QueueErrorCode.TELEGRAM_CACHE_PERSISTENCE_ERROR.value
            ) from exc
        return UploadResult(external_id=str(cached.cache_id))


def telegram_media_kind(container: NativeContainer | None) -> TelegramMediaKind:
    if container in {NativeContainer.MP3, NativeContainer.M4A}:
        return TelegramMediaKind.AUDIO
    return TelegramMediaKind.DOCUMENT


def sanitize_display_filename(
    artist: str | None,
    title: str | None,
    extension: str,
    *,
    max_length: int = MAX_DISPLAY_FILENAME_LENGTH,
) -> str:
    safe_extension = re.sub(r"[^a-z0-9]", "", extension.lower()) or "bin"
    parts = [_filename_part(value) for value in (artist, title)]
    stem = " - ".join(part for part in parts if part) or "track"
    suffix = f".{safe_extension}"
    stem = stem[: max(1, max_length - len(suffix))].rstrip(" .") or "track"
    return f"{stem}{suffix}"


def _filename_part(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFC", value)
    normalized = _UNSAFE_FILENAME.sub("_", normalized)
    normalized = " ".join(normalized.split())
    return normalized.strip(" .")


def _telegram_text(value: str | None) -> str | None:
    if not value:
        return None
    normalized = " ".join(_CONTROL.sub(" ", unicodedata.normalize("NFC", value)).split())
    return normalized[:MAX_TELEGRAM_TEXT_LENGTH] or None


def _extension(container: NativeContainer | None, path: Path) -> str:
    if container is not None and container not in {
        NativeContainer.UNKNOWN,
        NativeContainer.OTHER,
    }:
        return container.value
    return path.suffix.removeprefix(".") or "bin"


def _raise_upload_error(exc: TelegramGatewayError) -> NoReturn:
    if exc.retryable:
        raise UploadRetryableError(exc.code, retry_after_seconds=exc.retry_after_seconds) from exc
    raise UploadTerminalError(exc.code) from exc
