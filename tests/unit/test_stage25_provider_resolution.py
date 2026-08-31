from datetime import UTC, datetime, timedelta

from app.core.enums import MusicProviderName
from app.core.models import ProviderMediaCapabilities
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
