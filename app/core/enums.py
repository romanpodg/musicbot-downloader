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
    OTHER = "other"


class NativeContainer(StrEnum):
    MP3 = "mp3"
    M4A = "m4a"
    FLAC = "flac"
    OGG = "ogg"
    OTHER = "other"
