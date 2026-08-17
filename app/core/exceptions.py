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
