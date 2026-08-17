"""Provider-independent domain enumerations."""

from enum import StrEnum


class UserRole(StrEnum):
    USER = "USER"
    ADMIN = "ADMIN"
    OWNER = "OWNER"


class QualityProfile(StrEnum):
    MP3_128 = "MP3_128"
    MP3_320 = "MP3_320"
    AAC_256 = "AAC_256"
    LOSSLESS = "LOSSLESS"


class MusicProviderName(StrEnum):
    APPLE_MUSIC = "apple_music"
    BANDCAMP = "bandcamp"
    DEEZER = "deezer"
    QOBUZ = "qobuz"
    SOUNDCLOUD = "soundcloud"
    SPOTIFY = "spotify"
    TIDAL = "tidal"
    YOUTUBE_MUSIC = "youtube_music"


class NativeCodec(StrEnum):
    MP3 = "mp3"
    AAC = "aac"
    FLAC = "flac"
    VORBIS = "vorbis"
    OPUS = "opus"
    UNKNOWN = "unknown"
    OTHER = "other"


class NativeContainer(StrEnum):
    MP3 = "mp3"
    M4A = "m4a"
    FLAC = "flac"
    OGG = "ogg"
    WEBM = "webm"
    UNKNOWN = "unknown"
    OTHER = "other"


class ProviderRuntimeStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    ERROR = "ERROR"


class ProviderResolutionStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    NO_AVAILABLE_PROVIDER = "NO_AVAILABLE_PROVIDER"


class TrackMatchDecision(StrEnum):
    MATCHED = "MATCHED"
    NEW_TRACK = "NEW_TRACK"
    AMBIGUOUS = "AMBIGUOUS"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"


class TrackEvidenceCode(StrEnum):
    EXACT_SOURCE = "EXACT_SOURCE"
    ISRC_MATCH = "ISRC_MATCH"
    ISRC_CONFLICT = "ISRC_CONFLICT"
    TITLE_MATCH = "TITLE_MATCH"
    TITLE_CONFLICT = "TITLE_CONFLICT"
    ARTIST_MATCH = "ARTIST_MATCH"
    ARTIST_CONFLICT = "ARTIST_CONFLICT"
    DURATION_MATCH = "DURATION_MATCH"
    DURATION_LOOSE = "DURATION_LOOSE"
    DURATION_CONFLICT = "DURATION_CONFLICT"
    DURATION_UNKNOWN = "DURATION_UNKNOWN"
    VERSION_MATCH = "VERSION_MATCH"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    EXPLICIT_MATCH = "EXPLICIT_MATCH"
    EXPLICIT_CONFLICT = "EXPLICIT_CONFLICT"
    ALBUM_MATCH = "ALBUM_MATCH"


class ProviderDiscoveryStatus(StrEnum):
    INPUT = "INPUT"
    MATCHED = "MATCHED"
    NO_MATCH = "NO_MATCH"
    AMBIGUOUS = "AMBIGUOUS"
    UNAVAILABLE = "UNAVAILABLE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    ERROR = "ERROR"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
