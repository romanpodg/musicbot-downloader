"""Stable application error contract; errors intentionally contain no UI text."""


class MusicBotError(Exception):
    """Base error for failures callers may handle."""


class UnsupportedProvider(MusicBotError):
    pass


class InvalidTrackUrl(MusicBotError):
    pass


class ProviderUnavailable(MusicBotError):
    pass


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


class LocalizationError(MusicBotError):
    """A locale catalog or translation template is invalid."""


class LocalizationFormatError(LocalizationError):
    """A translation could not be formatted with the supplied values."""
