"""Reusable Stage 8 and Stage 9 application composition roots."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import cast

from aiogram import Dispatcher

from app.application.download import DownloadService, DownloadTrackUseCase
from app.application.search import SearchTracksUseCase
from app.application.ux import UserUxStateService, UxErrorService, UxFlowService, UxProgressService
from app.config import Settings
from app.core.enums import MusicProviderName
from app.core.models import TelegramBotIdentity
from app.core.provider_accounts import ProviderAuthorizationMethod
from app.i18n import LocalizationService
from app.providers.account_management import (
    ProviderAccountRuntimeProbe,
    ProviderRuntimeAccountBackend,
)
from app.providers.base import MusicProvider
from app.providers.deezer_authorization import (
    DeezerArlAuthorizationBoundary,
    DeezerArlAuthorizationDriver,
)
from app.providers.search_adapters import (
    DeezerSearchAdapter,
    SpotifySearchAdapter,
    TidalSearchAdapter,
)
from app.providers.spotify_authorization import (
    SpotifyAuthorizationBoundary,
    SpotifyPlaybackAuthorizationDriver,
    SpotifyWebApiAuthorizationDriver,
)
from app.providers.tidal_authorization import (
    TidalDeviceAuthorizationBoundary,
    TidalDeviceAuthorizationDriver,
)
from app.services.admin_management import AdministratorManagementService
from app.services.admin_overview import AdminOverviewService
from app.services.artifact_cleanup import (
    StaleArtifactCleanupManager,
    StaleArtifactCleanupService,
)
from app.services.artifacts import ActiveArtifactRegistry, DownloadArtifactManager
from app.services.authorization import TelegramAuthorizationService
from app.services.crash_recovery import CrashRecoveryService
from app.services.deep_links import DeepLinkRegistryService
from app.services.delivery import DeliveryPreparationService
from app.services.download_pipeline import DownloadPipeline, NativeDownloadBoundary
from app.services.download_requests import ExistingDeliverySubmissionService
from app.services.media import MediaProbe, Transcoder
from app.services.provider_accounts import ProviderAccountManagementService
from app.services.provider_authorization import ProviderAuthorizationCoordinator
from app.services.provider_health import ProviderHealthProbe, ProviderHealthService
from app.services.provider_resolution import ProviderResolver
from app.services.quality_resolution import QualityResolver
from app.services.queues import (
    DownloadQueueService,
    SubscriberLifecycleNotifier,
    UploadQueueService,
    WorkerSettingsService,
)
from app.services.recognized_track_resolution import RecognizedTrackResolutionAdapter
from app.services.runtime_prerequisites import TemporaryDiskGuard
from app.services.runtime_worker_control import RuntimeWorkerControlService
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
from app.services.track_recognition import RuleBasedRecognitionEngine, TrackRecognitionService
from app.services.track_resolution import ResolveTrackService
from app.services.track_search import TrackSearchProviderRegistry, TrackSearchService
from app.services.workers import DownloadWorkerBackend, QueueManager, UploadWorkerBackend
from app.storage import Database
from app.telegram import AiogramTelegramGateway, TelegramGateway
from app.telegram.admin_handlers import AdminHandlerDependencies, create_admin_router
from app.telegram.admin_management_presentation import AdminManagementPresentation
from app.telegram.admin_presentation import AdminPresentation
from app.telegram.handlers import TelegramHandlerDependencies, create_stage9_router
from app.telegram.keyboards import UxKeyboardFactory
from app.telegram.messages import UxMessageService
from app.telegram.presentation import TelegramPresentation
from app.telegram.provider_accounts_presentation import ProviderAccountsPresentation
from app.telegram.provider_authorization_ui import ProviderAuthorizationUiManager
from app.telegram.provider_health_presentation import ProviderHealthPresentation
from app.telegram.ux_handlers import UxHandlerDependencies, create_ux_router
from app.telegram.worker_control_presentation import WorkerControlPresentation

logger = logging.getLogger(__name__)


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
    worker_control: RuntimeWorkerControlService
    provider_health: ProviderHealthService
    provider_authorization: ProviderAuthorizationCoordinator
    provider_authorization_ui: ProviderAuthorizationUiManager
    provider_accounts: ProviderAccountManagementService
    deep_links: DeepLinkRegistryService
    crash_recovery: CrashRecoveryService
    artifact_cleanup: StaleArtifactCleanupService
    cleanup_manager: StaleArtifactCleanupManager
    ux_progress: UxProgressService

    async def start(self) -> None:
        try:
            await self.crash_recovery.recover_startup()
            await self.artifact_cleanup.sweep()
            await self.provider_accounts.reconcile_startup()
            await self.queue_manager.start()
            await self.delivery_fanout.start()
            await self.album_coordinator.start()
            await self.cleanup_manager.start()
        except BaseException:
            try:
                await self.stop()
            except Exception:
                logger.error("Partial component startup cleanup failed")
            raise

    async def stop(self) -> None:
        failures: list[Exception] = []
        for operation in (
            self.cleanup_manager.stop,
            self.album_coordinator.stop,
            self.delivery_fanout.stop,
            self.queue_manager.stop,
            self.provider_authorization_ui.close,
            self.provider_authorization.close,
            self.provider.close,
            self.stage8.close,
        ):
            try:
                await operation()
            except Exception as exc:
                failures.append(exc)
                logger.error("Application component shutdown failed")
        if failures:
            raise RuntimeError("one or more application components failed to stop") from failures[0]

    async def wait_terminated(self) -> None:
        await self.cleanup_manager.wait_terminated()


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
    artifact_registry = ActiveArtifactRegistry()
    artifacts = DownloadArtifactManager(settings.temp_dir, artifact_registry)
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
        disk_guard=TemporaryDiskGuard(
            settings.temp_dir,
            settings.temp_disk_min_free_bytes,
        ),
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
    worker_settings = WorkerSettingsService(database, settings)
    queue_manager = QueueManager(
        settings,
        worker_settings,
        downloads,
        uploads,
        download_backend,
        upload_backend,
        singleflight=singleflight,
    )
    i18n = LocalizationService(settings.supported_locales, settings.default_locale)
    users = TelegramUserService(database, i18n, owner_id=settings.owner_id)
    ux_states = UserUxStateService()
    ux_progress = UxProgressService(ux_states)
    search_registry = TrackSearchProviderRegistry(
        (
            SpotifySearchAdapter(provider),
            DeezerSearchAdapter(provider),
            TidalSearchAdapter(provider),
        )
    )
    search_use_case = SearchTracksUseCase(
        TrackSearchService(search_registry),
        TrackRecognitionService(RuleBasedRecognitionEngine()),
    )
    track_resolution = ResolveTrackService(database, provider)
    deep_links = DeepLinkRegistryService(
        database,
        provider,
        track_resolution,
        telegram_bot_id=stage8.bot_identity.telegram_bot_id,
    )
    requests = TelegramTrackRequestService(
        database,
        ResolveTrackAdapter(track_resolution, database=database, provider=provider),
        telegram_bot_id=stage8.bot_identity.telegram_bot_id,
        wake_event=delivery_wake,
    )
    download_service = DownloadService(
        DownloadTrackUseCase(
            RecognizedTrackResolutionAdapter(track_resolution),
            ExistingDeliverySubmissionService(database, requests),
        )
    )
    ux_flows = UxFlowService(users, ux_states, search_use_case, download_service)
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
    worker_control = RuntimeWorkerControlService(
        authorization,
        worker_settings,
        queue_manager,
    )
    provider_health = ProviderHealthService(cast(ProviderHealthProbe, provider), authorization)
    account_backend = ProviderRuntimeAccountBackend(
        cast(ProviderAccountRuntimeProbe, provider),
        authorization_methods={
            MusicProviderName.TIDAL: (ProviderAuthorizationMethod.BROWSER_DEVICE_LINK,),
            MusicProviderName.DEEZER: (ProviderAuthorizationMethod.SENSITIVE_SECRET,),
            MusicProviderName.SPOTIFY: (
                ProviderAuthorizationMethod.BROWSER_DEVICE_LINK,
                ProviderAuthorizationMethod.COMPOUND_CREDENTIALS,
            ),
        },
    )
    tidal_authorization = TidalDeviceAuthorizationDriver(
        cast(TidalDeviceAuthorizationBoundary, provider), account_backend
    )
    deezer_authorization = DeezerArlAuthorizationDriver(
        cast(DeezerArlAuthorizationBoundary, provider), account_backend
    )
    spotify_boundary = cast(SpotifyAuthorizationBoundary, provider)
    spotify_playback_authorization = SpotifyPlaybackAuthorizationDriver(
        spotify_boundary, account_backend
    )
    spotify_webapi_authorization = SpotifyWebApiAuthorizationDriver(
        spotify_boundary, account_backend
    )
    provider_authorization = ProviderAuthorizationCoordinator(
        {
            (
                MusicProviderName.TIDAL,
                ProviderAuthorizationMethod.BROWSER_DEVICE_LINK,
            ): tidal_authorization,
            (
                MusicProviderName.DEEZER,
                ProviderAuthorizationMethod.SENSITIVE_SECRET,
            ): deezer_authorization,
            (
                MusicProviderName.SPOTIFY,
                ProviderAuthorizationMethod.BROWSER_DEVICE_LINK,
            ): spotify_playback_authorization,
            (
                MusicProviderName.SPOTIFY,
                ProviderAuthorizationMethod.COMPOUND_CREDENTIALS,
            ): spotify_webapi_authorization,
        }
    )
    provider_accounts = ProviderAccountManagementService(
        account_backend,
        authorization,
        provider_authorization,
    )
    provider_accounts_presentation = ProviderAccountsPresentation(i18n)
    provider_authorization_ui = ProviderAuthorizationUiManager(
        provider_accounts, provider_accounts_presentation
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
                worker_control,
                WorkerControlPresentation(i18n),
                provider_health,
                ProviderHealthPresentation(i18n),
                provider_accounts,
                provider_accounts_presentation,
                provider_authorization_ui,
            )
        )
    )
    telegram_presentation = TelegramPresentation(i18n)
    dispatcher.include_router(
        create_ux_router(
            UxHandlerDependencies(
                users,
                ux_flows,
                UxMessageService(i18n),
                UxKeyboardFactory(i18n),
                UxErrorService(),
                download_service,
                requests,
                telegram_presentation,
            )
        )
    )
    dispatcher.include_router(
        create_stage9_router(
            TelegramHandlerDependencies(
                users,
                requests,
                telegram_presentation,
                media_requests,
                albums,
                deep_links,
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
    album_coordinator_service = TelegramAlbumCoordinator(
        database,
        track_resolution,
        stage8.gateway,
        i18n,
        album_wake_event=album_wake,
        delivery_wake_event=delivery_wake,
    )
    album_coordinator = TelegramAlbumCoordinatorManager(
        album_coordinator_service,
        wake_event=album_wake,
    )
    crash_recovery = CrashRecoveryService(
        database,
        uploads,
        singleflight,
        album_coordinator_service,
        telegram_bot_id=stage8.bot_identity.telegram_bot_id,
        delivery_max_attempts=settings.telegram_delivery_max_attempts,
    )
    artifact_cleanup = StaleArtifactCleanupService(
        database,
        settings.temp_dir,
        artifact_registry,
        stale_after_seconds=settings.temp_artifact_stale_after_seconds,
    )
    cleanup_manager = StaleArtifactCleanupManager(
        artifact_cleanup,
        interval_seconds=settings.temp_cleanup_interval_seconds,
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
        worker_control=worker_control,
        provider_health=provider_health,
        provider_authorization=provider_authorization,
        provider_authorization_ui=provider_authorization_ui,
        provider_accounts=provider_accounts,
        deep_links=deep_links,
        crash_recovery=crash_recovery,
        artifact_cleanup=artifact_cleanup,
        cleanup_manager=cleanup_manager,
        ux_progress=ux_progress,
    )
