"""Stage 30.5.2 six-provider quality-policy regression contract."""

from __future__ import annotations

from collections.abc import Iterable

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
from app.core.models import (
    DownloadPlan,
    DownloadProviderCandidate,
    NativeMediaInfo,
    PreparedSourceMedia,
)
from app.core.quality import plan_sort_key, plans_for_candidate, rejection_reason_for_candidate
from app.provider_integration import DEFAULT_PROVIDER_INTEGRATIONS, ProviderSearchIntegrationState
from app.providers.onthespot.capabilities import ONTHESPOT_CAPABILITIES

PROFILES = (
    QualityProfile.AAC_128,
    QualityProfile.AAC_256,
    QualityProfile.MP3_128,
    QualityProfile.MP3_320,
    QualityProfile.LOSSLESS,
)


def test_bandcamp_is_enabled_without_reordering_existing_search_providers() -> None:
    assert DEFAULT_PROVIDER_INTEGRATIONS.enabled_search_providers() == (
        MusicProviderName.SPOTIFY,
        MusicProviderName.DEEZER,
        MusicProviderName.TIDAL,
        MusicProviderName.YOUTUBE_MUSIC,
        MusicProviderName.QOBUZ,
        MusicProviderName.APPLE_MUSIC,
        MusicProviderName.BANDCAMP,
    )
    assert (
        DEFAULT_PROVIDER_INTEGRATIONS.for_provider(MusicProviderName.BANDCAMP).search
        is ProviderSearchIntegrationState.ENABLED
    )
    assert DEFAULT_PROVIDER_INTEGRATIONS.for_provider(MusicProviderName.SOUNDCLOUD).search is (
        ProviderSearchIntegrationState.DEFERRED
    )


def _candidate(
    provider: MusicProviderName,
    native: NativeMediaInfo | None,
) -> DownloadProviderCandidate:
    return DownloadProviderCandidate(
        track_id=3052,
        track_source_id=3052,
        provider=provider,
        provider_track_id=f"{provider.value}-track",
        runtime_status=ProviderRuntimeStatus.AVAILABLE,
        capabilities=ONTHESPOT_CAPABILITIES[provider],
        native_media_info=native,
    )


def _signature(plans: Iterable[DownloadPlan]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            plan.operation,
            plan.readiness,
            plan.source_expectation,
            plan.output_specification,
            plan.reason,
        )
        for plan in plans
    )


@pytest.mark.parametrize(
    ("provider", "native", "profile", "operation"),
    [
        *(
            (
                MusicProviderName.YOUTUBE_MUSIC,
                NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 128),
                profile,
                DownloadPlanOperation.DIRECT if profile is QualityProfile.AAC_128 else None,
            )
            for profile in PROFILES
        ),
        *(
            (
                MusicProviderName.APPLE_MUSIC,
                NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 256),
                profile,
                DownloadPlanOperation.DIRECT if profile is QualityProfile.AAC_256 else None,
            )
            for profile in PROFILES
        ),
        *(
            (
                MusicProviderName.QOBUZ,
                NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
                profile,
                (
                    DownloadPlanOperation.DIRECT
                    if profile is QualityProfile.LOSSLESS
                    else DownloadPlanOperation.TRANSCODE
                ),
            )
            for profile in PROFILES
        ),
        *(
            (
                MusicProviderName.SPOTIFY,
                NativeMediaInfo(NativeCodec.VORBIS, NativeContainer.OGG, 320),
                profile,
                None,
            )
            for profile in PROFILES
        ),
    ],
    ids=lambda value: value.value if hasattr(value, "value") else str(value),
)
def test_fixed_provider_exact_media_matrix(
    provider: MusicProviderName,
    native: NativeMediaInfo,
    profile: QualityProfile,
    operation: DownloadPlanOperation | None,
) -> None:
    """Fixed provider claims only become confirmed after exact media evidence."""

    plans = plans_for_candidate(_candidate(provider, native), profile)

    if operation is None:
        assert plans == ()
        return
    assert len(plans) == 1
    assert plans[0].operation is operation
    assert plans[0].readiness is DownloadPlanReadiness.CONFIRMED


@pytest.mark.parametrize(
    ("provider", "profile", "expected"),
    [
        (
            MusicProviderName.YOUTUBE_MUSIC,
            QualityProfile.AAC_128,
            (DownloadPlanOperation.DIRECT,),
        ),
        (
            MusicProviderName.APPLE_MUSIC,
            QualityProfile.AAC_256,
            (DownloadPlanOperation.DIRECT,),
        ),
        (
            MusicProviderName.QOBUZ,
            QualityProfile.AAC_128,
            (DownloadPlanOperation.TRANSCODE,),
        ),
        (MusicProviderName.QOBUZ, QualityProfile.LOSSLESS, (DownloadPlanOperation.DIRECT,)),
        (MusicProviderName.SPOTIFY, QualityProfile.MP3_320, ()),
    ],
)
def test_fixed_provider_capabilities_remain_preflight_until_media_is_observed(
    provider: MusicProviderName,
    profile: QualityProfile,
    expected: tuple[DownloadPlanOperation, ...],
) -> None:
    plans = plans_for_candidate(_candidate(provider, None), profile)

    assert tuple(plan.operation for plan in plans) == expected
    assert all(plan.readiness is DownloadPlanReadiness.REQUIRES_PREFLIGHT for plan in plans)


@pytest.mark.parametrize(
    ("provider", "profile", "expected"),
    [
        (
            MusicProviderName.DEEZER,
            QualityProfile.MP3_320,
            (DownloadPlanOperation.DIRECT, DownloadPlanOperation.TRANSCODE),
        ),
        (
            MusicProviderName.DEEZER,
            QualityProfile.LOSSLESS,
            (DownloadPlanOperation.DIRECT,),
        ),
        (
            MusicProviderName.TIDAL,
            QualityProfile.AAC_256,
            (DownloadPlanOperation.TRANSCODE,),
        ),
        (MusicProviderName.TIDAL, QualityProfile.LOSSLESS, (DownloadPlanOperation.DIRECT,)),
    ],
)
def test_variable_provider_capabilities_are_preflight_only(
    provider: MusicProviderName,
    profile: QualityProfile,
    expected: tuple[DownloadPlanOperation, ...],
) -> None:
    plans = plans_for_candidate(_candidate(provider, None), profile)

    assert tuple(plan.operation for plan in plans) == expected
    assert all(plan.readiness is DownloadPlanReadiness.REQUIRES_PREFLIGHT for plan in plans)
    assert all(plan.reason.value == "PROVIDER_PREFLIGHT_REQUIRED" for plan in plans)


@pytest.mark.parametrize(
    ("provider", "native", "profile", "operation"),
    [
        *(
            (
                provider,
                NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
                profile,
                (
                    DownloadPlanOperation.DIRECT
                    if profile is QualityProfile.LOSSLESS
                    else DownloadPlanOperation.TRANSCODE
                ),
            )
            for provider in (
                MusicProviderName.DEEZER,
                MusicProviderName.TIDAL,
                MusicProviderName.QOBUZ,
            )
            for profile in PROFILES
        ),
        (
            MusicProviderName.DEEZER,
            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 128),
            QualityProfile.MP3_128,
            DownloadPlanOperation.DIRECT,
        ),
        (
            MusicProviderName.DEEZER,
            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 320),
            QualityProfile.MP3_320,
            DownloadPlanOperation.DIRECT,
        ),
        (
            MusicProviderName.TIDAL,
            NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 128),
            QualityProfile.AAC_128,
            DownloadPlanOperation.DIRECT,
        ),
        (
            MusicProviderName.TIDAL,
            NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 256),
            QualityProfile.AAC_256,
            DownloadPlanOperation.DIRECT,
        ),
    ],
)
def test_exact_media_evidence_uses_provider_neutral_safe_policy(
    provider: MusicProviderName,
    native: NativeMediaInfo,
    profile: QualityProfile,
    operation: DownloadPlanOperation,
) -> None:
    plans = plans_for_candidate(_candidate(provider, native), profile)

    assert len(plans) == 1
    assert plans[0].operation is operation
    assert plans[0].readiness is DownloadPlanReadiness.CONFIRMED


@pytest.mark.parametrize(
    ("native", "profile", "rejection"),
    [
        (
            NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 128),
            QualityProfile.AAC_256,
            QualityCandidateRejectionReason.UPSCALE_FORBIDDEN,
        ),
        (
            NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 128),
            QualityProfile.MP3_128,
            QualityCandidateRejectionReason.LOSSY_TRANSCODE_FORBIDDEN,
        ),
        (
            NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 128),
            QualityProfile.LOSSLESS,
            QualityCandidateRejectionReason.LOSSLESS_REQUIRED,
        ),
        (
            NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 256),
            QualityProfile.AAC_128,
            QualityCandidateRejectionReason.LOSSY_TRANSCODE_FORBIDDEN,
        ),
        (
            NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 256),
            QualityProfile.MP3_320,
            QualityCandidateRejectionReason.LOSSY_TRANSCODE_FORBIDDEN,
        ),
        (
            NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 256),
            QualityProfile.LOSSLESS,
            QualityCandidateRejectionReason.LOSSLESS_REQUIRED,
        ),
        (
            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 128),
            QualityProfile.MP3_320,
            QualityCandidateRejectionReason.UPSCALE_FORBIDDEN,
        ),
        (
            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 128),
            QualityProfile.AAC_128,
            QualityCandidateRejectionReason.LOSSY_TRANSCODE_FORBIDDEN,
        ),
        (
            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 128),
            QualityProfile.LOSSLESS,
            QualityCandidateRejectionReason.LOSSLESS_REQUIRED,
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
            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 320),
            QualityProfile.LOSSLESS,
            QualityCandidateRejectionReason.LOSSLESS_REQUIRED,
        ),
    ],
)
def test_lossy_media_never_converts_to_another_profile(
    native: NativeMediaInfo,
    profile: QualityProfile,
    rejection: QualityCandidateRejectionReason,
) -> None:
    candidate = _candidate(MusicProviderName.DEEZER, native)

    assert plans_for_candidate(candidate, profile) == ()
    assert rejection_reason_for_candidate(candidate, profile) is rejection


@pytest.mark.parametrize(
    "provider",
    (MusicProviderName.QOBUZ, MusicProviderName.DEEZER, MusicProviderName.TIDAL),
)
def test_verified_flac_has_identical_quality_policy_for_every_provider(
    provider: MusicProviderName,
) -> None:
    flac = NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC)
    expected = _signature(
        plans_for_candidate(_candidate(MusicProviderName.QOBUZ, flac), QualityProfile.MP3_320)
    )

    assert (
        _signature(plans_for_candidate(_candidate(provider, flac), QualityProfile.MP3_320))
        == expected
    )


def test_plan_sort_key_is_the_only_quality_tier_ordering_used_by_the_contract() -> None:
    direct_confirmed = plans_for_candidate(
        _candidate(
            MusicProviderName.YOUTUBE_MUSIC,
            NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 128),
        ),
        QualityProfile.AAC_128,
    )[0]
    direct_preflight = plans_for_candidate(
        _candidate(MusicProviderName.YOUTUBE_MUSIC, None), QualityProfile.AAC_128
    )[0]
    transcode_confirmed = plans_for_candidate(
        _candidate(
            MusicProviderName.QOBUZ, NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC)
        ),
        QualityProfile.AAC_128,
    )[0]
    transcode_preflight = plans_for_candidate(
        _candidate(MusicProviderName.QOBUZ, None), QualityProfile.AAC_128
    )[0]

    assert sorted(
        (transcode_preflight, transcode_confirmed, direct_preflight, direct_confirmed),
        key=plan_sort_key,
    ) == [direct_confirmed, direct_preflight, transcode_confirmed, transcode_preflight]


def test_provider_decryption_and_application_transcode_provenance_stay_distinct() -> None:
    apple_source = PreparedSourceMedia(
        MusicProviderName.APPLE_MUSIC,
        "apple-aac256",
        codec=NativeCodec.AAC,
        container=NativeContainer.M4A,
        bitrate_kbps=256,
        native_encoded=True,
        provider_decrypted=True,
        upstream_quality_transcoded=False,
    )
    qobuz_source = PreparedSourceMedia(
        MusicProviderName.QOBUZ,
        "qobuz-flac",
        codec=NativeCodec.FLAC,
        container=NativeContainer.FLAC,
        lossless=True,
        native_encoded=True,
    )

    assert apple_source.provider_decrypted is True
    assert apple_source.upstream_quality_transcoded is False
    assert (
        plans_for_candidate(
            _candidate(
                MusicProviderName.APPLE_MUSIC,
                NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 256),
            ),
            QualityProfile.AAC_256,
        )[0].operation
        is DownloadPlanOperation.DIRECT
    )
    assert qobuz_source.lossless is True
    assert (
        plans_for_candidate(
            _candidate(
                MusicProviderName.QOBUZ, NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC)
            ),
            QualityProfile.MP3_320,
        )[0].operation
        is DownloadPlanOperation.TRANSCODE
    )
