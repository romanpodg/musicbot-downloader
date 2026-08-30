"""Focused repository exports."""

from app.storage.repositories.batch_download import BatchDownloadRepository
from app.storage.repositories.deep_links import DeepLinkRegistryRepository
from app.storage.repositories.download_lifecycle import DownloadLifecycleRepository
from app.storage.repositories.download_preferences import UserDownloadPreferencesRepository
from app.storage.repositories.operational_audit import OperationalAuditRepository
from app.storage.repositories.singleflight import SingleFlightRepository
from app.storage.repositories.telegram_album import TelegramAlbumRepository
from app.storage.repositories.telegram_cache import TelegramFileCacheRepository
from app.storage.repositories.telegram_context import TelegramContextRepository
from app.storage.repositories.telegram_delivery import TelegramDeliveryRepository
from app.storage.repositories.track_sources import TrackSourceRepository, UpsertSourceResult
from app.storage.repositories.tracks import TrackRepository
from app.storage.repositories.users import UserRepository

__all__ = [
    "TrackRepository",
    "DeepLinkRegistryRepository",
    "OperationalAuditRepository",
    "TrackSourceRepository",
    "UpsertSourceResult",
    "UserRepository",
    "DownloadJobRepository",
    "DownloadLifecycleRepository",
    "UserDownloadPreferencesRepository",
    "RuntimeSettingsRepository",
    "SingleFlightRepository",
    "TelegramFileCacheRepository",
    "TelegramContextRepository",
    "TelegramDeliveryRepository",
    "TelegramAlbumRepository",
    "UploadJobRepository",
    "BatchDownloadRepository",
]
from app.storage.repositories.queue import (
    DownloadJobRepository,
    RuntimeSettingsRepository,
    UploadJobRepository,
)
