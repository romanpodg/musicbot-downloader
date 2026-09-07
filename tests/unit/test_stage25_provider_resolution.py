from datetime import UTC, datetime, timedelta

from app.core.enums import (
    DownloadPlanOperation,
    DownloadPlanReadiness,
    DownloadPlanReason,
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    QualityProfile,
)
from app.core.models import (
    DownloadPlan,
    OutputSpecification,
    ProviderMediaCapabilities,
    SourceMediaRequirement,
)
from app.core.provider_resolution import (
    CanonicalMediaIdentity,
    MatchMethod,
    ProviderCandidate,
    ProviderCandidateRanker,
    match_media,
)
from app.services.provider_account_selection import (
    AccountHealthState,
    ProviderAccountHealth,
    ProviderAccountSelector,
)
from app.services.provider_fallback import FallbackDecision, fallback_decision


def identity(title: str = "Song", **kwargs: object) -> CanonicalMediaIdentity:
    return CanonicalMediaIdentity.from_values(title=title, artist="Artist", **kwargs)


def quality_plan(
    provider: MusicProviderName,
    media_id: str,
    operation: DownloadPlanOperation,
) -> DownloadPlan:
    return DownloadPlan(
        track_id=1,
        track_source_id=1,
        provider=provider,
        provider_track_id=media_id,
        requested_profile=QualityProfile.AAC_128,
        source_expectation=(
            SourceMediaRequirement(required_lossless=True)
            if operation is DownloadPlanOperation.TRANSCODE
            else SourceMediaRequirement(required_codec=NativeCodec.AAC, required_bitrate_kbps=128)
        ),
        output_specification=OutputSpecification(
            codec=NativeCodec.AAC,
            container=NativeContainer.M4A,
            bitrate_kbps=128,
            lossless=False,
        ),
        operation=operation,
        readiness=DownloadPlanReadiness.CONFIRMED,
        reason=(
            DownloadPlanReason.LOSSLESS_TO_REQUESTED_LOSSY
            if operation is DownloadPlanOperation.TRANSCODE
            else DownloadPlanReason.NATIVE_EXACT_MATCH
        ),
    )


def test_exact_isrc_is_strong_and_normalized() -> None:
    result = match_media(identity(isrc="US-ABC-12-34567"), identity(isrc="usabc1234567"))
    assert result.method is MatchMethod.ISRC_EXACT
    assert result.automatic_fallback_eligible


def test_duration_mismatch_rejects_metadata_match() -> None:
    result = match_media(identity(duration_ms=100_000), identity(duration_ms=104_000))
    assert result.method is MatchMethod.REJECTED
    assert not result.automatic_fallback_eligible


def test_version_mismatch_is_vetoed_without_isrc() -> None:
    result = match_media(identity(), identity("Song (Live)"))
    assert result.method is MatchMethod.REJECTED


def test_exact_isrc_allows_display_version_marker_difference() -> None:
    result = match_media(
        identity(isrc="USABC1234567"), identity("Song (Remaster)", isrc="USABC1234567")
    )
    assert result.method is MatchMethod.ISRC_EXACT


def test_ranker_is_deterministic_and_prefers_source_provider() -> None:
    media = ProviderMediaCapabilities(supports_lossy=True)
    source_identity = identity(duration_ms=100_000)
    source = ProviderCandidate(
        MusicProviderName.TIDAL,
        "b",
        source_identity,
        match_media(source_identity, source_identity),
        media,
    )
    other = ProviderCandidate(
        MusicProviderName.DEEZER,
        "a",
        source_identity,
        match_media(source_identity, source_identity),
        media,
    )
    ranked = ProviderCandidateRanker((MusicProviderName.DEEZER, MusicProviderName.TIDAL)).rank(
        (other, source), source_provider=MusicProviderName.TIDAL
    )
    assert ranked[0] is source


def test_quality_plan_beats_source_affinity_and_optional_provider_priority() -> None:
    media = ProviderMediaCapabilities(supports_lossy=True)
    source_identity = identity(duration_ms=100_000)
    qobuz = ProviderCandidate(
        MusicProviderName.QOBUZ,
        "qobuz-flac",
        source_identity,
        match_media(source_identity, source_identity),
        media,
    )
    ytm = ProviderCandidate(
        MusicProviderName.YOUTUBE_MUSIC,
        "ytm-aac128",
        source_identity,
        match_media(source_identity, source_identity),
        media,
    )
    ranked = ProviderCandidateRanker((MusicProviderName.QOBUZ,)).rank(
        (qobuz, ytm),
        source_provider=MusicProviderName.QOBUZ,
        quality_plans={
            (MusicProviderName.QOBUZ, "qobuz-flac"): quality_plan(
                MusicProviderName.QOBUZ, "qobuz-flac", DownloadPlanOperation.TRANSCODE
            ),
            (MusicProviderName.YOUTUBE_MUSIC, "ytm-aac128"): quality_plan(
                MusicProviderName.YOUTUBE_MUSIC,
                "ytm-aac128",
                DownloadPlanOperation.DIRECT,
            ),
        },
    )
    assert ranked == (ytm, qobuz)


def test_quality_plan_filters_incompatible_recognition_provider() -> None:
    media = ProviderMediaCapabilities(supports_lossy=True)
    source_identity = identity(duration_ms=100_000)
    apple = ProviderCandidate(
        MusicProviderName.APPLE_MUSIC,
        "apple-aac256",
        source_identity,
        match_media(source_identity, source_identity),
        media,
    )
    qobuz = ProviderCandidate(
        MusicProviderName.QOBUZ,
        "qobuz-flac",
        source_identity,
        match_media(source_identity, source_identity),
        media,
    )
    ranked = ProviderCandidateRanker().rank(
        (apple, qobuz),
        source_provider=MusicProviderName.APPLE_MUSIC,
        quality_plans={
            (MusicProviderName.QOBUZ, "qobuz-flac"): quality_plan(
                MusicProviderName.QOBUZ, "qobuz-flac", DownloadPlanOperation.DIRECT
            )
        },
    )
    assert ranked == (qobuz,)


def test_apple_direct_beats_qobuz_transcode_even_when_qobuz_is_recognized() -> None:
    media = ProviderMediaCapabilities(supports_lossy=True)
    source_identity = identity(duration_ms=100_000)
    apple = ProviderCandidate(
        MusicProviderName.APPLE_MUSIC,
        "apple-aac256",
        source_identity,
        match_media(source_identity, source_identity),
        media,
    )
    qobuz = ProviderCandidate(
        MusicProviderName.QOBUZ,
        "qobuz-flac",
        source_identity,
        match_media(source_identity, source_identity),
        media,
    )
    ranked = ProviderCandidateRanker((MusicProviderName.QOBUZ,)).rank(
        (qobuz, apple),
        source_provider=MusicProviderName.QOBUZ,
        quality_plans={
            (MusicProviderName.QOBUZ, "qobuz-flac"): quality_plan(
                MusicProviderName.QOBUZ, "qobuz-flac", DownloadPlanOperation.TRANSCODE
            ),
            (MusicProviderName.APPLE_MUSIC, "apple-aac256"): quality_plan(
                MusicProviderName.APPLE_MUSIC,
                "apple-aac256",
                DownloadPlanOperation.DIRECT,
            ),
        },
    )
    assert ranked[0] is apple


def test_cooldown_and_auth_failed_accounts_are_skipped() -> None:
    now = datetime.now(UTC)
    selector = ProviderAccountSelector()
    healthy = ProviderAccountHealth(MusicProviderName.TIDAL, "a")
    cooldown = ProviderAccountHealth(
        MusicProviderName.TIDAL,
        "b",
        AccountHealthState.COOLDOWN,
        cooldown_until=now + timedelta(minutes=1),
    )
    assert selector.eligible((healthy, cooldown), now) == (healthy,)
    assert selector.record_failure(healthy, auth=True).state is AccountHealthState.AUTH_FAILED


def test_fallback_policy_keeps_processing_and_delivery_local() -> None:
    from app.core.enums import DownloadFailureCode

    assert (
        fallback_decision(DownloadFailureCode.PROVIDER_AUTH, another_account=True)
        is FallbackDecision.SAME_PROVIDER_NEXT_ACCOUNT
    )
    assert (
        fallback_decision(DownloadFailureCode.MEDIA_NOT_FOUND, another_provider=True)
        is FallbackDecision.NEXT_PROVIDER
    )
    assert (
        fallback_decision(DownloadFailureCode.PROCESSING, another_provider=True)
        is FallbackDecision.STOP
    )
