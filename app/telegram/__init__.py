"""Telegram transport adapter boundary; no polling or handlers live here."""

from app.telegram.aiogram_gateway import AiogramTelegramGateway
from app.telegram.gateway import (
    TelegramCachedMediaSpec,
    TelegramDeliveryReceipt,
    TelegramGateway,
    TelegramGatewayError,
    TelegramUploadSpec,
)

__all__ = [
    "AiogramTelegramGateway",
    "TelegramCachedMediaSpec",
    "TelegramDeliveryReceipt",
    "TelegramGateway",
    "TelegramGatewayError",
    "TelegramUploadSpec",
]
