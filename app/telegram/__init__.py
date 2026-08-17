"""Telegram transport adapter boundary; no polling or handlers live here."""

from app.telegram.aiogram_gateway import AiogramTelegramGateway
from app.telegram.gateway import (
    TelegramGateway,
    TelegramGatewayError,
    TelegramUploadSpec,
)

__all__ = [
    "AiogramTelegramGateway",
    "TelegramGateway",
    "TelegramGatewayError",
    "TelegramUploadSpec",
]
