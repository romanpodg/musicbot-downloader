from __future__ import annotations

import pytest

from app.core.enums import (
    DownloadPlanOperation,
    DownloadPlanReadiness,
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    ProviderRuntimeStatus,
    QualityCandidateRejectionReason,
    QualityProfile,
)
from app.core.models import DownloadProviderCandidate, NativeMediaInfo
from app.core.quality import (
    QUALITY_OUTPUTS,
    plans_for_candidate,
    rejection_reason_for_candidate,
)
from app.providers.onthespot.capabilities import ONTHESPOT_CAPABILITIES


def _candidate(
    native: NativeMediaInfo | None,
    *,
    provider: MusicProviderName = MusicProviderName.QOBUZ,
) -> DownloadProviderCandidate:
    return DownloadProviderCandidate(
        track_id=10,
        track_source_id=20,
        provider=provider,
        provider_track_id="provider-track",
        runtime_status=ProviderRuntimeStatus.AVAILABLE,
        capabilities=ONTHESPOT_CAPABILITIES[provider],
        native_media_info=native,
    )


@pytest.mark.parametrize(
    ("native", "profile", "operation"),
    [
        (
            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 128),
            QualityProfile.MP3_128,
            DownloadPlanOperation.DIRECT,
        ),
        (
            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 320),
            QualityProfile.MP3_320,
            DownloadPlanOperation.DIRECT,
        ),
        (
            NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 256),
            QualityProfile.AAC_256,
            DownloadPlanOperation.DIRECT,
        ),
        (
            NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
            QualityProfile.LOSSLESS,
            DownloadPlanOperation.DIRECT,
        ),
        (
            NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
            QualityProfile.MP3_128,
            DownloadPlanOperation.TRANSCODE,
        ),
        (
            NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
            QualityProfile.MP3_320,
            DownloadPlanOperation.TRANSCODE,
        ),
        (
            NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
            QualityProfile.AAC_256,
            DownloadPlanOperation.TRANSCODE,
        ),
    ],
)
def test_confirmed_safe_transformations(
    native: NativeMediaInfo,
    profile: QualityProfile,
    operation: DownloadPlanOperation,
) -> None:
    plans = plans_for_candidate(_candidate(native), profile)

    assert len(plans) == 1
    assert plans[0].operation is operation
    assert plans[0].readiness is DownloadPlanReadiness.CONFIRMED
    assert plans[0].requested_profile is profile


@pytest.mark.parametrize(
    ("native", "profile", "reason"),
    [
        (
            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 128),
            QualityProfile.MP3_320,
            QualityCandidateRejectionReason.UPSCALE_FORBIDDEN,
        ),
        (
            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 320),
            QualityProfile.MP3_128,
            QualityCandidateRejectionReason.LOSSY_TRANSCODE_FORBIDDEN,
        ),
        (
            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 320),
            QualityProfile.AAC_256,
            QualityCandidateRejectionReason.LOSSY_TRANSCODE_FORBIDDEN,
        ),
        (
            NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 128),
            QualityProfile.AAC_256,
            QualityCandidateRejectionReason.UPSCALE_FORBIDDEN,
        ),
        (
            NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 256),
            QualityProfile.MP3_320,
            QualityCandidateRejectionReason.LOSSY_TRANSCODE_FORBIDDEN,
        ),
        (
            NativeMediaInfo(NativeCodec.VORBIS, NativeContainer.OGG, 160),
            QualityProfile.MP3_128,
            QualityCandidateRejectionReason.LOSSY_TRANSCODE_FORBIDDEN,
        ),
        (
            NativeMediaInfo(NativeCodec.VORBIS, NativeContainer.OGG, 320),
            QualityProfile.MP3_320,
            QualityCandidateRejectionReason.LOSSY_TRANSCODE_FORBIDDEN,
        ),
        (
            NativeMediaInfo(NativeCodec.VORBIS, NativeContainer.OGG, 320),
            QualityProfile.LOSSLESS,
            QualityCandidateRejectionReason.LOSSLESS_REQUIRED,
        ),
        (
            NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 256),
            QualityProfile.LOSSLESS,
            QualityCandidateRejectionReason.LOSSLESS_REQUIRED,
        ),
        (
            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 320),
            QualityProfile.LOSSLESS,
            QualityCandidateRejectionReason.LOSSLESS_REQUIRED,
        ),
    ],
)
def test_forbidden_transformations_have_no_plan(
    native: NativeMediaInfo,
    profile: QualityProfile,
    reason: QualityCandidateRejectionReason,
) -> None:
    candidate = _candidate(native)
    assert plans_for_candidate(candidate, profile) == ()
    assert rejection_reason_for_candidate(candidate, profile) is reason


def test_output_contracts_are_centralized_and_distinct_from_native_media() -> None:
    assert set(QUALITY_OUTPUTS) == set(QualityProfile)
    assert QUALITY_OUTPUTS[QualityProfile.MP3_320].bitrate_kbps == 320
    assert QUALITY_OUTPUTS[QualityProfile.AAC_256].container is NativeContainer.M4A
    assert QUALITY_OUTPUTS[QualityProfile.LOSSLESS].preserve_source is True
    assert QUALITY_OUTPUTS[QualityProfile.LOSSLESS].codec is None


@pytest.mark.parametrize(
    ("provider", "native", "profile", "operation"),
    [
        (
            MusicProviderName.APPLE_MUSIC,
            NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 256),
            QualityProfile.AAC_256,
            DownloadPlanOperation.DIRECT,
        ),
        (
            MusicProviderName.BANDCAMP,
            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 128),
            QualityProfile.MP3_128,
            DownloadPlanOperation.DIRECT,
        ),
        (
            MusicProviderName.SOUNDCLOUD,
            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 128),
            QualityProfile.MP3_128,
            DownloadPlanOperation.DIRECT,
        ),
        (
            MusicProviderName.QOBUZ,
            NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
            QualityProfile.LOSSLESS,
            DownloadPlanOperation.DIRECT,
        ),
        (
            MusicProviderName.QOBUZ,
            NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
            QualityProfile.AAC_256,
            DownloadPlanOperation.TRANSCODE,
        ),
    ],
)
def test_provider_facts_are_evaluated_by_generic_policy(
    provider: MusicProviderName,
    native: NativeMediaInfo,
    profile: QualityProfile,
    operation: DownloadPlanOperation,
) -> None:
    plan = plans_for_candidate(_candidate(native, provider=provider), profile)[0]
    assert plan.provider is provider
    assert plan.operation is operation


@pytest.mark.parametrize(
    ("provider", "native", "profile"),
    [
        (
            MusicProviderName.SPOTIFY,
            NativeMediaInfo(NativeCodec.VORBIS, NativeContainer.OGG, 320),
            QualityProfile.MP3_320,
        ),
        (
            MusicProviderName.YOUTUBE_MUSIC,
            NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 128),
            QualityProfile.AAC_256,
        ),
    ],
)
def test_known_provider_lossy_media_is_not_upgraded(
    provider: MusicProviderName,
    native: NativeMediaInfo,
    profile: QualityProfile,
) -> None:
    assert plans_for_candidate(_candidate(native, provider=provider), profile) == ()


@pytest.mark.parametrize("profile", list(QualityProfile))
def test_spotify_vorbis_has_no_valid_current_user_profile(profile: QualityProfile) -> None:
    candidate = _candidate(
        NativeMediaInfo(NativeCodec.VORBIS, NativeContainer.OGG, 320),
        provider=MusicProviderName.SPOTIFY,
    )
    assert plans_for_candidate(candidate, profile) == ()


def test_tidal_unknown_stream_returns_explicit_lossless_preflight() -> None:
    plans = plans_for_candidate(
        _candidate(None, provider=MusicProviderName.TIDAL),
        QualityProfile.LOSSLESS,
    )
    assert len(plans) == 1
    assert plans[0].readiness is DownloadPlanReadiness.REQUIRES_PREFLIGHT
    assert plans[0].operation is DownloadPlanOperation.DIRECT
    assert plans[0].source_expectation.required_lossless is True


def test_deezer_preflight_keeps_exact_and_lossless_strategies_separate() -> None:
    plans = plans_for_candidate(
        _candidate(None, provider=MusicProviderName.DEEZER),
        QualityProfile.MP3_320,
    )
    assert [(plan.operation, plan.source_expectation.required_lossless) for plan in plans] == [
        (DownloadPlanOperation.DIRECT, None),
        (DownloadPlanOperation.TRANSCODE, True),
    ]
    assert all(plan.readiness is DownloadPlanReadiness.REQUIRES_PREFLIGHT for plan in plans)
