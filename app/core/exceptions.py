"""Stable application error contract; errors intentionally contain no UI text."""

from app.core.enums import DownloadFailureCode, QueueErrorCode
from app.core.models import DownloadAttempt


class MusicBotError(Exception):
    """Base error for failures callers may handle."""


class UnsupportedProvider(MusicBotError):
    pass


class InvalidTrackUrl(MusicBotError):
    pass


class UnsupportedMediaType(InvalidTrackUrl):
    """A recognized provider URL targets an out-of-scope non-track entity."""


class UnsupportedAlbum(UnsupportedMediaType):
    """The pinned provider boundary cannot reliably resolve this album."""


class AlbumTooLarge(MusicBotError):
    """The provider release exceeds the bounded durable snapshot limit."""


class AlbumResolutionFailed(MusicBotError):
    """A recognized album could not be resolved through its provider boundary."""


class ProviderUnavailable(MusicBotError):
    pass


class ProviderOperationTimeout(ProviderUnavailable):
    """A bounded provider operation exceeded its execution timeout."""


class MetadataUnavailable(MusicBotError):
    pass


class ProviderAuthenticationError(MusicBotError):
    pass


class ConfigurationError(MusicBotError):
    pass


class DatabaseError(MusicBotError):
    pass


class DatabaseConcurrencyError(DatabaseError):
    """A transient write conflict that may be retried as a whole transaction."""


class IdempotencyKeyConflict(MusicBotError):
    """One bot-scoped idempotency key was reused for a different request."""


class DeepLinkNotFound(MusicBotError):
    """A bot-scoped registry token does not exist."""


class TrackSourceOwnershipConflict(DatabaseError):
    """A provider identity is already owned by a different canonical Track."""


class TrackNotFound(DatabaseError):
    """The requested canonical Track does not exist."""


class LocalizationError(MusicBotError):
    """A locale catalog or translation template is invalid."""


class LocalizationFormatError(LocalizationError):
    """A translation could not be formatted with the supplied values."""


class DownloadPipelineError(MusicBotError):
    """Typed terminal Stage 6 failure with safe structured attempt diagnostics."""

    def __init__(
        self, code: DownloadFailureCode, attempts: tuple[DownloadAttempt, ...] = ()
    ) -> None:
        super().__init__()
        self.code = code
        self.attempts = attempts


class QueueServiceError(MusicBotError):
    """Typed queue operation failure without presentation text."""

    def __init__(self, code: QueueErrorCode) -> None:
        super().__init__()
        self.code = code


class QueueFullError(QueueServiceError):
    def __init__(self) -> None:
        super().__init__(QueueErrorCode.QUEUE_FULL)


class QueueJobNotFoundError(QueueServiceError):
    def __init__(self) -> None:
        super().__init__(QueueErrorCode.JOB_NOT_FOUND)


class SubscriberNotFoundError(QueueServiceError):
    def __init__(self) -> None:
        super().__init__(QueueErrorCode.SUBSCRIBER_NOT_FOUND)


class InvalidRequestKeyError(QueueServiceError):
    def __init__(self) -> None:
        super().__init__(QueueErrorCode.INVALID_REQUEST_KEY)


class WorkerLimitError(QueueServiceError):
    def __init__(self) -> None:
        super().__init__(QueueErrorCode.WORKER_LIMIT_EXCEEDED)


class UploadRetryableError(MusicBotError):
    """Delivery failed transiently and may be retried by Stage 7."""

    def __init__(
        self,
        code: QueueErrorCode | str = QueueErrorCode.UPLOAD_RETRYABLE,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__()
        self.code = QueueErrorCode(code)
        self.retry_after_seconds = retry_after_seconds


class UploadTerminalError(MusicBotError):
    """Delivery failed permanently for this artifact."""

    def __init__(self, code: QueueErrorCode | str = QueueErrorCode.UPLOAD_TERMINAL) -> None:
        super().__init__()
        self.code = QueueErrorCode(code)


class DeliveryInvariantError(MusicBotError):
    """A READY subscriber has no matching active completed-result cache entry."""

    code = "READY_CACHE_MISSING"


class SubscriberNotReadyError(MusicBotError):
    code = "SUBSCRIBER_NOT_READY"


class TelegramCacheEntryNotFoundError(MusicBotError):
    code = "TELEGRAM_CACHE_ENTRY_NOT_FOUND"
