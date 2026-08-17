"""Reusable Stage 8 service composition; intentionally starts no bot update loop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.config import Settings
from app.core.models import TelegramBotIdentity
from app.services.delivery import DeliveryPreparationService
from app.services.queues import SubscriberLifecycleNotifier
from app.services.telegram_cache import TelegramFileCacheService
from app.services.telegram_upload import TelegramCacheUploadExecutor
from app.storage import Database
from app.telegram import AiogramTelegramGateway, TelegramGateway


@dataclass(slots=True)
class Stage8Components:
    gateway: TelegramGateway
    bot_identity: TelegramBotIdentity
    telegram_cache: TelegramFileCacheService
    upload_executor: TelegramCacheUploadExecutor
    delivery_preparation: DeliveryPreparationService

    async def close(self) -> None:
        await self.gateway.close()


async def compose_stage8(
    database: Database,
    settings: Settings,
    *,
    gateway: TelegramGateway | None = None,
    download_wake_event: asyncio.Event | None = None,
    notifier: SubscriberLifecycleNotifier | None = None,
) -> Stage8Components:
    token, cache_chat_id = settings.telegram_cache_configuration()
    selected_gateway = gateway or AiogramTelegramGateway(token)
    try:
        identity = await selected_gateway.get_bot_identity()
    except Exception:
        if gateway is None:
            await selected_gateway.close()
        raise
    cache = TelegramFileCacheService(database)
    return Stage8Components(
        gateway=selected_gateway,
        bot_identity=identity,
        telegram_cache=cache,
        upload_executor=TelegramCacheUploadExecutor(
            database, cache, selected_gateway, cache_chat_id=cache_chat_id
        ),
        delivery_preparation=DeliveryPreparationService(
            database,
            telegram_bot_id=identity.telegram_bot_id,
            max_size=settings.queue_max_size,
            download_wake_event=download_wake_event,
            notifier=notifier,
        ),
    )
