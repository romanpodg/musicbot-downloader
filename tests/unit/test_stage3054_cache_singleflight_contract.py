from __future__ import annotations

from dataclasses import fields
from types import SimpleNamespace

import pytest

from app.core.download_preferences import EffectiveDownloadProfile
from app.core.enums import DeliveryMode, FormatPreference, QualityPreference
from app.core.media_artifact import MediaArtifactSpec
from app.core.models import DownloadArtifactMetadata
from app.core.telegram_artifact_cache import TelegramCacheKey


def _profile(*, delivery_mode: DeliveryMode = DeliveryMode.AUDIO) -> EffectiveDownloadProfile:
    return EffectiveDownloadProfile(
        requested_quality=QualityPreference.HIGH,
        effective_quality=QualityPreference.HIGH,
        requested_format=FormatPreference.M4A,
        effective_format=FormatPreference.M4A,
        delivery_mode=delivery_mode,
        embed_metadata=True,
        embed_cover=True,
    )


def _request(provider: str, media_id: str, *, delivery_mode: str = "audio") -> SimpleNamespace:
    return SimpleNamespace(
        provider=provider,
        provider_media_id=media_id,
        effective_quality="high",
        effective_format="m4a",
        delivery_mode=delivery_mode,
        embed_metadata=True,
        embed_cover=True,
        media_title="Canonical track",
        media_artist="Artist",
        media_album="Album",
    )


def test_stage3054_stage24_key_is_admission_identity_not_acquisition_provenance() -> None:
    apple = TelegramCacheKey.from_request(_request("apple_music", "apple-admitted"))
    qobuz = TelegramCacheKey.from_request(_request("qobuz", "qobuz-admitted"))

    assert apple is not None and qobuz is not None
    assert apple.provider == "apple_music"
    assert apple.provider_media_id == "apple-admitted"
    assert apple.fingerprint != qobuz.fingerprint
    assert '"provider":"apple_music"' in apple.canonical_json()
    # A successful Qobuz fallback does not rewrite this request-derived key.
    assert "qobuz" not in apple.canonical_json()


@pytest.mark.parametrize(
    ("case", "quality", "format_"),
    [("ytm_aac128", "standard", "m4a"), ("apple_aac256", "high", "m4a")],
)
def test_stage3054_artifact_fingerprint_is_provider_neutral(
    case: str, quality: str, format_: str
) -> None:
    profile = EffectiveDownloadProfile(
        requested_quality=QualityPreference(quality),
        effective_quality=QualityPreference(quality),
        requested_format=FormatPreference(format_),
        effective_format=FormatPreference(format_),
        delivery_mode=DeliveryMode.AUDIO,
        embed_metadata=True,
        embed_cover=True,
    )
    # These represent YTM/Apple direct and Qobuz-transcoded acquisition paths.
    direct = MediaArtifactSpec.from_profile(
        profile, metadata={"title": "Canonical track", "artist": "Artist"}
    )
    qobuz_transcode = MediaArtifactSpec.from_profile(
        profile, metadata={"title": "Canonical track", "artist": "Artist"}
    )

    assert direct.fingerprint == qobuz_transcode.fingerprint
    assert case in {"ytm_aac128", "apple_aac256"}
    assert "provider" not in direct.canonical_json()
    assert "provider_media_id" not in direct.canonical_json()
    assert "provider_account_id" not in direct.canonical_json()


def test_stage3054_delivery_mode_omission_is_characterized_without_key_change() -> None:
    audio = TelegramCacheKey.from_request(_request("apple_music", "apple-admitted"))
    document = TelegramCacheKey.from_request(
        _request("apple_music", "apple-admitted", delivery_mode="document")
    )

    assert audio is not None and document is not None
    assert audio.delivery_mode == "audio"
    assert document.delivery_mode == "document"
    assert '"delivery_mode"' not in audio.canonical_json()
    assert audio.fingerprint == document.fingerprint


def test_stage3054_artwork_identity_is_not_invented_from_provider_artwork() -> None:
    spec = MediaArtifactSpec.from_profile(_profile(), metadata={"title": "Canonical track"})

    assert spec.artwork_identity is None
    assert '"artwork_identity":null' in spec.canonical_json()


def test_stage3054_account_provenance_does_not_leak_into_artifact_metadata() -> None:
    artifact_fields = {field.name for field in fields(DownloadArtifactMetadata)}

    assert "source_provider" in artifact_fields
    assert "source_provider_track_id" in artifact_fields
    assert "provider_account_id" not in artifact_fields
