"""Focused Stage 30.2 YouTube Music and AAC_128 regressions."""

from pathlib import Path
from unittest.mock import Mock

from app.core.enums import (
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    ProviderHealthStatus,
    ProviderRuntimeStatus,
    QualityProfile,
)
from app.core.models import (
    DownloadProviderCandidate,
    NativeMediaInfo,
    ProviderHealthEntry,
)
from app.core.quality import QUALITY_OUTPUTS, plans_for_candidate
from app.provider_integration import DEFAULT_PROVIDER_INTEGRATIONS
from app.providers.onthespot.capabilities import ONTHESPOT_CAPABILITIES
from app.providers.onthespot.provider import OnTheSpotProvider
from app.services.media import Transcoder
from app.services.provider_search_readiness import (
    ProviderSearchReadinessStatus,
    evaluate_provider_search_readiness,
)
from app.telegram.presentation import (
    encode_track_quality,
    parse_track_quality,
)


def _candidate(media: NativeMediaInfo) -> DownloadProviderCandidate:
    provider = MusicProviderName.YOUTUBE_MUSIC
    return DownloadProviderCandidate(
        track_id=1,
        track_source_id=2,
        provider=provider,
        provider_track_id="video_id",
        runtime_status=ProviderRuntimeStatus.AVAILABLE,
        capabilities=ONTHESPOT_CAPABILITIES[provider],
        native_media_info=media,
    )


def test_stage302_manifest_and_readiness_promote_tokenless_ytm() -> None:
    provider = MusicProviderName.YOUTUBE_MUSIC
    assert DEFAULT_PROVIDER_INTEGRATIONS.enabled_search_providers() == (
        MusicProviderName.SPOTIFY,
        MusicProviderName.DEEZER,
        MusicProviderName.TIDAL,
        provider,
        MusicProviderName.QOBUZ,
        MusicProviderName.APPLE_MUSIC,
    )
    assert DEFAULT_PROVIDER_INTEGRATIONS.managed_account_providers() == (
        MusicProviderName.TIDAL,
        MusicProviderName.DEEZER,
        MusicProviderName.SPOTIFY,
        MusicProviderName.QOBUZ,
        MusicProviderName.APPLE_MUSIC,
    )
    integration = DEFAULT_PROVIDER_INTEGRATIONS.for_provider(provider)
    assert integration.authorization_methods == ()
    assert (
        evaluate_provider_search_readiness(
            integration,
            ONTHESPOT_CAPABILITIES[provider],
            runtime_searchable=False,
            health=ProviderHealthEntry(provider, ProviderHealthStatus.AUTH_REQUIRED, False, True),
        ).status
        is ProviderSearchReadinessStatus.UNAVAILABLE
    )


def test_stage302_strict_music_youtube_track_url_scope() -> None:
    provider = OnTheSpotProvider(Mock())
    reference = provider.detect_url("https://music.youtube.com/watch?v=abc_123-XYZ")
    assert reference.provider is MusicProviderName.YOUTUBE_MUSIC
    assert reference.provider_track_id == "abc_123-XYZ"
    assert reference.source_url == "https://music.youtube.com/watch?v=abc_123-XYZ"


def test_stage302_quality_matrix_and_lossless_fallback() -> None:
    ytm = _candidate(NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 128))
    direct = plans_for_candidate(ytm, QualityProfile.AAC_128)
    assert len(direct) == 1
    assert direct[0].operation.value == "DIRECT"
    assert direct[0].reason.value == "NATIVE_EXACT_MATCH"
    assert not plans_for_candidate(ytm, QualityProfile.AAC_256)
    assert not plans_for_candidate(ytm, QualityProfile.MP3_128)
    assert not plans_for_candidate(ytm, QualityProfile.MP3_320)
    assert not plans_for_candidate(ytm, QualityProfile.LOSSLESS)

    lossless = _candidate(NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC))
    transcode = plans_for_candidate(lossless, QualityProfile.AAC_128)
    assert len(transcode) == 1
    assert transcode[0].operation.value == "TRANSCODE"
    assert transcode[0].output_specification == QUALITY_OUTPUTS[QualityProfile.AAC_128]


def test_stage302_transcoder_emits_exact_aac128_m4a_command(tmp_path: Path) -> None:
    command = Transcoder(tmp_path).command(
        tmp_path / "source.flac",
        tmp_path / "output.m4a.partial",
        QUALITY_OUTPUTS[QualityProfile.AAC_128],
        {},
    )
    assert "-c:a" in command and command[command.index("-c:a") + 1] == "aac"
    assert "-b:a" in command and command[command.index("-b:a") + 1] == "128k"
    assert command[command.index("-f") + 1] == "ipod"
    assert "shell=True" not in " ".join(command)


def test_stage302_telegram_quality_callback_round_trip() -> None:
    callback = encode_track_quality(123, QualityProfile.AAC_128)
    assert len(callback.encode()) <= 64
    parsed = parse_track_quality(callback)
    assert parsed is not None
    assert parsed.quality_profile is QualityProfile.AAC_128
