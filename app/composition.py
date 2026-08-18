"""Reusable Stage 8 and Stage 9 application composition roots."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import cast

from aiogram import Dispatcher

from app.config import Settings
from app.core.models import TelegramBotIdentity
from app.i18n import LocalizationService
from app.providers.base import MusicProvider
from app.services.admin_management import AdministratorManagementService
from app.services.admin_overview import AdminOverviewService
from app.services.artifacts import DownloadArtifactManager
from app.services.authorization import TelegramAuthorizationService
from app.services.delivery import DeliveryPreparationService
from app.services.download_pipeline import DownloadPipeline, NativeDownloadBoundary
from app.services.media import MediaProbe, Transcoder
from app.services.provider_resolution import ProviderResolver
from app.services.quality_resolution import QualityResolver
from app.services.queues import (
    DownloadQueueService,
    SubscriberLifecycleNotifier,
    UploadQueueService,
    WorkerSettingsService,
)
from app.services.singleflight import SingleFlightService, SubscriberNotifier
from app.services.telegram_album_coordinator import (
    TelegramAlbumCoordinator,
    TelegramAlbumCoordinatorManager,
)
from app.services.telegram_albums import TelegramAlbumRequestService, TelegramAlbumResolver
from app.services.telegram_cache import TelegramFileCacheService
from app.services.telegram_delivery import TelegramDeliveryFanoutManager, TelegramDeliveryWorker
from app.services.telegram_media_requests import TelegramMediaRequestService
from app.services.telegram_requests import ResolveTrackAdapter, TelegramTrackRequestService
from app.services.telegram_upload import TelegramCacheUploadExecutor
from app.services.telegram_users import TelegramUserService
from app.services.track_resolution import ResolveTrackService
from app.services.workers import DownloadWorkerBackend, QueueManager, UploadWorkerBackend
from app.storage import Database
from app.telegram import AiogramTelegramGateway, TelegramGateway
from app.telegram.admin_handlers import AdminHandlerDependencies, create_admin_router
from app.telegram.admin_management_presentation import AdminManagementPresentation
from app.telegram.admin_presentation import AdminPresentation
from app.telegram.handlers import TelegramHandlerDependencies, create_stage9_router
from app.telegram.presentation import TelegramPresentation


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


@dataclass(slots=True)
class Stage9Components:
    stage8: Stage8Components
    dispatcher: Dispatcher
    queue_manager: QueueManager
    delivery_fanout: TelegramDeliveryFanoutManager
    album_coordinator: TelegramAlbumCoordinatorManager
    provider: MusicProvider
    authorization: TelegramAuthorizationService
    admin_overview: AdminOverviewService
    admin_management: AdministratorManagementService

    async def start(self) -> None:
        await self.queue_manager.start()
        await self.delivery_fanout.start()
        await self.album_coordinator.start()

    async def stop(self) -> None:
        await self.album_coordinator.stop()
        await self.delivery_fanout.stop()
        await self.queue_manager.stop()
        await self.provider.close()
        await self.stage8.close()


async def compose_stage9(
    database: Database,
    settings: Settings,
    provider: MusicProvider,
    *,
    gateway: TelegramGateway | None = None,
) -> Stage9Components:
    """Compose the queue/cache/user-delivery runtime without starting polling."""

    download_wake = asyncio.Event()
    upload_wake = asyncio.Event()
    delivery_wake = asyncio.Event()
    album_wake = asyncio.Event()
    notifier = SubscriberNotifier()
    artifacts = DownloadArtifactManager(settings.temp_dir)
    downloads = DownloadQueueService(
        database,
        max_size=settings.queue_max_size,
        wake_event=download_wake,
        subscriber_notifier=notifier,
    )
    uploads = UploadQueueService(
        database,
        artifacts,
        wake_event=upload_wake,
        subscriber_notifier=notifier,
    )
    singleflight = SingleFlightService(
        database,
        max_size=settings.queue_max_size,
        download_wake_event=download_wake,
        upload_wake_event=upload_wake,
        notifier=notifier,
        upload_queue=uploads,
    )
    stage8 = await compose_stage8(
        database,
        settings,
        gateway=gateway,
        download_wake_event=download_wake,
        notifier=notifier,
    )
    pipeline = DownloadPipeline(
        database,
        QualityResolver(ProviderResolver(database, provider)),
        cast(NativeDownloadBoundary, provider),
        artifacts,
        MediaProbe(settings.temp_dir, settings.ffprobe_binary),
        Transcoder(
            settings.temp_dir,
            settings.ffmpeg_binary,
            timeout=settings.transcode_timeout_seconds,
        ),
        download_timeout=settings.download_timeout_seconds,
    )
    download_backend = DownloadWorkerBackend(
        database,
        pipeline,
        artifacts,
        wake_event=download_wake,
        subscriber_notifier=notifier,
    )
    upload_backend = UploadWorkerBackend(
        database,
        stage8.upload_executor,
        uploads,
        wake_event=upload_wake,
        subscriber_notifier=notifier,
    )
    queue_manager = QueueManager(
        settings,
        WorkerSettingsService(database, settings),
        downloads,
        uploads,
        download_backend,
        upload_backend,
        singleflight=singleflight,
    )
    i18n = LocalizationService(settings.supported_locales, settings.default_locale)
    users = TelegramUserService(database, i18n, owner_id=settings.owner_id)
    track_resolution = ResolveTrackService(database, provider)
    requests = TelegramTrackRequestService(
        database,
        ResolveTrackAdapter(track_resolution, database=database, provider=provider),
        telegram_bot_id=stage8.bot_identity.telegram_bot_id,
        wake_event=delivery_wake,
    )
    albums = TelegramAlbumRequestService(
        database,
        TelegramAlbumResolver(provider),
        telegram_bot_id=stage8.bot_identity.telegram_bot_id,
        wake_event=album_wake,
    )
    media_requests = TelegramMediaRequestService(provider, requests, albums)
    authorization = TelegramAuthorizationService(database, owner_id=settings.owner_id)
    admin_overview = AdminOverviewService(
        database,
        authorization,
        queue_manager,
        stage8.telegram_cache,
        telegram_bot_id=stage8.bot_identity.telegram_bot_id,
    )
    admin_management = AdministratorManagementService(
        database,
        authorization,
        owner_id=settings.owner_id,
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_admin_router(
            AdminHandlerDependencies(
                users,
                admin_overview,
                AdminPresentation(i18n),
                admin_management,
                AdminManagementPresentation(i18n),
            )
        )
    )
    dispatcher.include_router(
        create_stage9_router(
            TelegramHandlerDependencies(
                users,
                requests,
                TelegramPresentation(i18n),
                media_requests,
                albums,
            )
        )
    )
    delivery_worker = TelegramDeliveryWorker(
        database,
        stage8.delivery_preparation,
        stage8.telegram_cache,
        stage8.gateway,
        i18n,
        max_attempts=settings.telegram_delivery_max_attempts,
        wake_event=delivery_wake,
    )
    fanout = TelegramDeliveryFanoutManager(
        delivery_worker,
        workers=settings.telegram_delivery_workers,
        wake_event=delivery_wake,
    )
    album_coordinator = TelegramAlbumCoordinatorManager(
        TelegramAlbumCoordinator(
            database,
            track_resolution,
            stage8.gateway,
            i18n,
            album_wake_event=album_wake,
            delivery_wake_event=delivery_wake,
        ),
        wake_event=album_wake,
    )
    return Stage9Components(
        stage8=stage8,
        dispatcher=dispatcher,
        queue_manager=queue_manager,
        delivery_fanout=fanout,
        album_coordinator=album_coordinator,
        provider=provider,
        authorization=authorization,
        admin_overview=admin_overview,
        admin_management=admin_management,
    )
