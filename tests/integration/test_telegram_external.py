from __future__ import annotations

import os
import wave
from pathlib import Path

import pytest

from app.telegram import AiogramTelegramGateway, TelegramUploadSpec


@pytest.mark.external
async def test_real_telegram_gateway_uploads_synthetic_document(tmp_path: Path) -> None:
    token = os.getenv("BOT_TOKEN", "").strip()
    chat_id_raw = os.getenv("TELEGRAM_TEST_CACHE_CHAT_ID", "").strip()
    if not token or not chat_id_raw:
        pytest.skip("BOT_TOKEN and TELEGRAM_TEST_CACHE_CHAT_ID are required")
    try:
        chat_id: int | str = int(chat_id_raw)
    except ValueError:
        chat_id = chat_id_raw

    fixture = tmp_path / "stage8-synthetic.wav"
    with wave.open(str(fixture), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 800)

    gateway = AiogramTelegramGateway(token)
    try:
        identity = await gateway.get_bot_identity()
        receipt = await gateway.upload_document(
            TelegramUploadSpec(
                chat_id=chat_id,
                file_path=fixture,
                display_filename="stage8-synthetic.wav",
            )
        )
        assert receipt.telegram_bot_id == identity.telegram_bot_id
        assert receipt.file_id and receipt.file_unique_id
    finally:
        await gateway.close()
