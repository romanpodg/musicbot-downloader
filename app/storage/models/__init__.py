"""SQLAlchemy model exports used by Alembic and repositories."""

from app.storage.models.base import Base
from app.storage.models.batch_download import BatchDownloadItem, BatchDownloadRequest
from app.storage.models.deep_link import DeepLinkRegistryEntry
from app.storage.models.download_lifecycle import (
    DownloadDelivery,
    DownloadLifecycleJob,
    DownloadRequestRecord,
)
from app.storage.models.download_preferences import UserDownloadPreferencesRecord
from app.storage.models.operational_audit import OperationalAuditEvent
from app.storage.models.provider_resolution import (
    DownloadProviderAttemptRecord,
    DownloadProviderCandidateRecord,
    ProviderAttempt,
    ProviderCandidateRecord,
)
from app.storage.models.queue import (
    DownloadFlight,
    DownloadJob,
    JobSubscriber,
    RuntimeSettings,
    UploadJob,
)
from app.storage.models.telegram_album import TelegramAlbumItem, TelegramAlbumRequest
from app.storage.models.telegram_artifact_cache import TelegramArtifactCacheEntry
from app.storage.models.telegram_cache import TelegramFileCache
from app.storage.models.telegram_context import TelegramChannelBinding, TelegramChatPolicy
from app.storage.models.telegram_delivery import TelegramDeliveryRequest
from app.storage.models.track import Track
from app.storage.models.track_source import TrackSource
from app.storage.models.user import User

__all__ = [
    "Base",
    "DownloadJob",
    "DownloadRequestRecord",
    "DownloadLifecycleJob",
    "DownloadDelivery",
    "UserDownloadPreferencesRecord",
    "BatchDownloadRequest",
    "BatchDownloadItem",
    "DeepLinkRegistryEntry",
    "DownloadFlight",
    "JobSubscriber",
    "OperationalAuditEvent",
    "RuntimeSettings",
    "Track",
    "TrackSource",
    "TelegramFileCache",
    "TelegramArtifactCacheEntry",
    "TelegramChannelBinding",
    "TelegramChatPolicy",
    "TelegramDeliveryRequest",
    "TelegramAlbumItem",
    "TelegramAlbumRequest",
    "UploadJob",
    "User",
    "DownloadProviderCandidateRecord",
    "DownloadProviderAttemptRecord",
    "ProviderCandidateRecord",
    "ProviderAttempt",
]
