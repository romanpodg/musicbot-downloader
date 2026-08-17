"""Capabilities of the exactly pinned OnTheSpot download implementation."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from app.core.enums import MusicProviderName, NativeCodec, NativeContainer
from app.core.models import ProviderCapabilities, ProviderMediaCapabilities


def _media(
    *,
    lossy: bool | None,
    lossless: bool | None,
    codecs: tuple[NativeCodec, ...] = (),
    containers: tuple[NativeContainer, ...] = (),
    bitrates: tuple[int, ...] = (),
) -> ProviderMediaCapabilities:
    return ProviderMediaCapabilities(
        known=True,
        supports_lossy=lossy,
        supports_lossless=lossless,
        native_codecs=frozenset(codecs),
        native_containers=frozenset(containers),
        bitrate_options_kbps=frozenset(bitrates),
    )


_CAPABILITIES = {
    MusicProviderName.APPLE_MUSIC: ProviderCapabilities(
        True,
        True,
        True,
        True,
        _media(
            lossy=True,
            lossless=False,
            codecs=(NativeCodec.AAC,),
            containers=(NativeContainer.M4A,),
            bitrates=(256,),
        ),
    ),
    MusicProviderName.BANDCAMP: ProviderCapabilities(
        True,
        True,
        True,
        False,
        _media(
            lossy=True,
            lossless=False,
            codecs=(NativeCodec.MP3,),
            containers=(NativeContainer.MP3,),
            bitrates=(128,),
        ),
    ),
    MusicProviderName.DEEZER: ProviderCapabilities(
        True,
        True,
        True,
        True,
        _media(
            lossy=True,
            lossless=True,
            codecs=(NativeCodec.MP3, NativeCodec.FLAC),
            containers=(NativeContainer.MP3, NativeContainer.FLAC),
            bitrates=(128, 256, 320),
        ),
    ),
    MusicProviderName.QOBUZ: ProviderCapabilities(
        True,
        True,
        True,
        True,
        _media(
            lossy=False,
            lossless=True,
            codecs=(NativeCodec.FLAC,),
            containers=(NativeContainer.FLAC,),
        ),
    ),
    MusicProviderName.SOUNDCLOUD: ProviderCapabilities(
        True,
        True,
        True,
        False,
        _media(
            lossy=True,
            lossless=False,
            codecs=(NativeCodec.MP3, NativeCodec.AAC),
            containers=(NativeContainer.MP3, NativeContainer.M4A),
            bitrates=(128, 256),
        ),
    ),
    MusicProviderName.SPOTIFY: ProviderCapabilities(
        True,
        True,
        True,
        True,
        _media(
            lossy=True,
            lossless=False,
            codecs=(NativeCodec.VORBIS,),
            containers=(NativeContainer.OGG,),
            bitrates=(160, 320),
        ),
    ),
    MusicProviderName.TIDAL: ProviderCapabilities(
        True,
        True,
        True,
        True,
        _media(
            lossy=True,
            lossless=True,
            codecs=(NativeCodec.AAC, NativeCodec.FLAC),
            containers=(NativeContainer.M4A, NativeContainer.FLAC),
        ),
    ),
    MusicProviderName.YOUTUBE_MUSIC: ProviderCapabilities(
        True,
        True,
        True,
        False,
        _media(
            lossy=True,
            lossless=False,
            codecs=(NativeCodec.AAC,),
            containers=(NativeContainer.M4A,),
            bitrates=(128,),
        ),
    ),
}

ONTHESPOT_CAPABILITIES: Final = MappingProxyType(_CAPABILITIES)
PROVIDER_ORDER: Final = tuple(MusicProviderName)
_PROVIDER_INDEX: Final = {provider: index for index, provider in enumerate(PROVIDER_ORDER)}


def provider_sort_key(provider: MusicProviderName) -> int:
    """Stable declaration order only; deliberately not a quality ranking."""

    return _PROVIDER_INDEX[provider]
