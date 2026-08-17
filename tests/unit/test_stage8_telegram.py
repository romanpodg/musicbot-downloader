from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.methods import GetMe

from app.core.enums import NativeContainer, QueueErrorCode, TelegramMediaKind
from app.logging import SecretRedactionFilter
from app.services.telegram_upload import sanitize_display_filename, telegram_media_kind
from app.telegram.aiogram_gateway import AiogramTelegramGateway, _normalized_error
from app.telegram.gateway import TelegramUploadSpec


@pytest.mark.parametrize(
    ("container", "expected"),
    [
        (NativeContainer.MP3, TelegramMediaKind.AUDIO),
        (NativeContainer.M4A, TelegramMediaKind.AUDIO),
        (NativeContainer.FLAC, TelegramMediaKind.DOCUMENT),
        (NativeContainer.OGG, TelegramMediaKind.DOCUMENT),
        (None, TelegramMediaKind.DOCUMENT),
    ],
)
def test_media_kind_preserves_transport_quality(
    container: NativeContainer | None, expected: TelegramMediaKind
) -> None:
    assert telegram_media_kind(container) is expected


@pytest.mark.parametrize(
    ("artist", "title"),
    [
        ("A/B", "C\\D"),
        ("A:B", "C*D?"),
        ("A\x00B", "C\x1fD"),
        (" . ", "   "),
        ("Исполнитель", "Песня 🎵"),
        ("A" * 300, "B" * 300),
    ],
)
def test_display_filename_is_safe_bounded_and_unicode_aware(artist: str, title: str) -> None:
    value = sanitize_display_filename(artist, title, "flac")
    assert value.endswith(".flac")
    assert 1 <= len(value) <= 180
    assert not any(character in value for character in '<>:"/\\|?*\x00\x1f')
    assert not value.removesuffix(".flac").endswith((" ", "."))


def test_display_filename_falls_back_for_empty_metadata() -> None:
    assert sanitize_display_filename(None, None, "") == "track.bin"


def test_secret_filter_redacts_token_and_token_bearing_url() -> None:
    token = "123456:SUPER-SECRET"
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "request failed at https://api.telegram.org/bot%s/sendAudio",
        (token,),
        None,
    )
    assert SecretRedactionFilter((token,)).filter(record)
    rendered = record.getMessage()
    assert token not in rendered
    assert "bot[REDACTED]" in rendered


@pytest.mark.parametrize(
    ("exception", "code", "retryable", "retry_after"),
    [
        (
            TelegramRetryAfter(GetMe(), "limited", 12),
            QueueErrorCode.TELEGRAM_RATE_LIMITED.value,
            True,
            12.0,
        ),
        (
            TelegramNetworkError(GetMe(), "offline"),
            QueueErrorCode.TELEGRAM_TRANSPORT_ERROR.value,
            True,
            None,
        ),
        (
            TelegramForbiddenError(GetMe(), "forbidden"),
            QueueErrorCode.TELEGRAM_PERMISSION_DENIED.value,
            False,
            None,
        ),
        (
            TelegramUnauthorizedError(GetMe(), "unauthorized"),
            QueueErrorCode.TELEGRAM_AUTH_FAILED.value,
            False,
            None,
        ),
    ],
)
def test_aiogram_errors_are_structurally_normalized(
    exception: Exception, code: str, retryable: bool, retry_after: float | None
) -> None:
    normalized = _normalized_error(exception)  # type: ignore[arg-type]
    assert normalized.code == code
    assert normalized.retryable is retryable
    assert normalized.retry_after_seconds == retry_after


class FakeSession:
    async def close(self) -> None:
        return None


class FakeAiogramBot:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.calls: list[tuple[str, object]] = []

    async def get_me(self) -> Any:
        self.calls.append(("get_me", None))
        return SimpleNamespace(id=991, username="cache_bot")

    async def send_audio(self, **kwargs: object) -> Any:
        self.calls.append(("audio", kwargs))
        return SimpleNamespace(
            chat=SimpleNamespace(id=-1001),
            message_id=7,
            audio=SimpleNamespace(file_id="audio-id", file_unique_id="audio-unique", file_size=10),
            document=None,
        )

    async def send_document(self, **kwargs: object) -> Any:
        self.calls.append(("document", kwargs))
        return SimpleNamespace(
            chat=SimpleNamespace(id=-1001),
            message_id=8,
            audio=None,
            document=SimpleNamespace(file_id="doc-id", file_unique_id="doc-unique", file_size=11),
        )


async def test_aiogram_gateway_normalizes_identity_audio_and_document(tmp_path: Path) -> None:
    gateway = AiogramTelegramGateway("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789")
    fake = FakeAiogramBot()
    gateway._bot = fake  # type: ignore[assignment]  # noqa: SLF001
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"audio")
    spec = TelegramUploadSpec(-1001, path, "Artist - Title.mp3")

    first = await gateway.get_bot_identity()
    second = await gateway.get_bot_identity()
    audio = await gateway.upload_audio(spec)
    document = await gateway.upload_document(spec)
    await gateway.close()

    assert first == second
    assert [call[0] for call in fake.calls].count("get_me") == 1
    assert audio.media_kind is TelegramMediaKind.AUDIO
    assert audio.file_id == "audio-id"
    assert document.media_kind is TelegramMediaKind.DOCUMENT
    assert document.file_id == "doc-id"
