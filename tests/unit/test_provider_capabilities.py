from app.core.enums import MusicProviderName, NativeCodec, NativeContainer
from app.providers.onthespot.capabilities import ONTHESPOT_CAPABILITIES, PROVIDER_ORDER


def test_registry_covers_every_local_music_provider() -> None:
    assert set(ONTHESPOT_CAPABILITIES) == set(MusicProviderName)
    assert set(PROVIDER_ORDER) == set(MusicProviderName)


def test_unknown_sample_properties_are_not_reported_as_unsupported() -> None:
    tidal = ONTHESPOT_CAPABILITIES[MusicProviderName.TIDAL]
    assert tidal.media.known is True
    assert tidal.media.supports_lossy is True
    assert tidal.media.supports_lossless is True
    assert tidal.media.max_sample_rate_hz is None
    assert tidal.media.max_bit_depth is None
    assert tidal.media.bitrate_options_kbps == frozenset()


def test_codec_and_container_are_separate() -> None:
    apple = ONTHESPOT_CAPABILITIES[MusicProviderName.APPLE_MUSIC]
    assert apple.media.native_codecs == frozenset({NativeCodec.AAC})
    assert apple.media.native_containers == frozenset({NativeContainer.M4A})
