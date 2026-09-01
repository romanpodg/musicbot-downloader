from __future__ import annotations

import pytest

from app.core.download_preferences import (
    DownloadProfileResolver,
    FormatUnavailable,
    InvalidDownloadPreferences,
    UserDownloadPreferences,
)
from app.core.enums import (
    DeliveryMode,
    FormatPreference,
    NativeCodec,
    NativeContainer,
    QualityPreference,
)
from app.core.models import NativeMediaInfo, ProviderCapabilities, ProviderMediaCapabilities
from app.telegram.download_preferences_callbacks import (
    PreferenceSetting,
    encode_preference_callback,
    parse_preference_callback,
)


def capabilities(*media: NativeMediaInfo) -> ProviderCapabilities:
    return ProviderCapabilities(
        True,
        True,
        True,
        False,
        ProviderMediaCapabilities(
            known=True,
            supports_lossy=True,
            supports_lossless=any(item.codec is NativeCodec.FLAC for item in media),
            native_containers=frozenset(item.container for item in media if item.container),
            potential_media=media,
        ),
    )


def test_defaults_and_best_available_are_deterministic() -> None:
    prefs = UserDownloadPreferences(1)
    profile = DownloadProfileResolver().resolve(
        prefs,
        capabilities(
            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 320),
            NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
        ),
    )
    assert prefs.quality is QualityPreference.BEST_AVAILABLE
    assert profile.effective_quality is QualityPreference.LOSSLESS
    assert not profile.fallback_applied


def test_lossless_falls_back_to_high_when_track_is_lossy_only() -> None:
    profile = DownloadProfileResolver().resolve(
        UserDownloadPreferences(1, quality=QualityPreference.LOSSLESS),
        capabilities(NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 320)),
    )
    assert profile.effective_quality is QualityPreference.HIGH
    assert profile.fallback_applied


def test_high_does_not_select_lossless() -> None:
    profile = DownloadProfileResolver().resolve(
        UserDownloadPreferences(1, quality=QualityPreference.HIGH),
        capabilities(NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC)),
    )
    assert profile.effective_quality is QualityPreference.STANDARD
    assert profile.fallback_applied


def test_invalid_quality_format_and_unsupported_format() -> None:
    with pytest.raises(InvalidDownloadPreferences):
        UserDownloadPreferences(1, quality=QualityPreference.LOSSLESS, format=FormatPreference.MP3)
    with pytest.raises(FormatUnavailable):
        DownloadProfileResolver().resolve(
            UserDownloadPreferences(1, format=FormatPreference.M4A),
            capabilities(NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC)),
        )


def test_profile_preserves_delivery_and_flags() -> None:
    profile = DownloadProfileResolver().resolve(
        UserDownloadPreferences(
            1,
            delivery_mode=DeliveryMode.DOCUMENT,
            embed_metadata=False,
            embed_cover=False,
        ),
        capabilities(NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 320)),
    )
    assert profile.delivery_mode is DeliveryMode.DOCUMENT
    assert not profile.embed_metadata and not profile.embed_cover


def test_settings_callback_is_strict_and_typed() -> None:
    encoded = encode_preference_callback(PreferenceSetting.QUALITY, QualityPreference.HIGH.value)
    parsed = parse_preference_callback(encoded)
    assert parsed is not None and parsed.setting is PreferenceSetting.QUALITY
    assert parse_preference_callback("dp1:q:not-a-quality") is None
    assert parse_preference_callback("dp1:m:maybe") is None
    reset = encode_preference_callback(PreferenceSetting.RESET, "reset")
    assert parse_preference_callback(reset) is not None
    assert parse_preference_callback("dp1:r:not-reset") is None
