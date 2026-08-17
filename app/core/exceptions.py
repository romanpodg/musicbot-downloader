"""Stable application error contract; errors intentionally contain no UI text."""

from app.core.enums import DownloadFailureCode
from app.core.models import DownloadAttempt


class MusicBotError(Exception):
    """Base error for failures callers may handle."""


class UnsupportedProvider(MusicBotError):
    pass


class InvalidTrackUrl(MusicBotError):
    pass


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
