"""Provider-independent matching and candidate-resolution value objects.

Stage 25 deliberately keeps identity, candidate discovery, ranking, and account
selection separate from the existing Stage 21 download lifecycle.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from app.core.download_preferences import EffectiveDownloadProfile
from app.core.enums import MusicProviderName, QualityPreference
from app.core.models import MediaCapabilities
from app.core.search import Track
from app.core.track_identity import normalize_isrc, normalize_text, normalize_title_artist


class MediaType(StrEnum):
    TRACK = "track"


class MatchMethod(StrEnum):
    ISRC_EXACT = "ISRC_EXACT"
    METADATA_STRONG = "METADATA_STRONG"
    METADATA_MEDIUM = "METADATA_MEDIUM"
    WEAK = "WEAK"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class CanonicalMediaIdentity:
    media_type: MediaType = MediaType.TRACK
    isrc: str | None = None
    title: str = ""
    artist: str = ""
    album: str | None = None
    duration_ms: int | None = None
    version_markers: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.title.strip() or not self.artist.strip():
            raise ValueError("canonical identity requires title and artist")
        object.__setattr__(self, "isrc", normalize_isrc(self.isrc))
        object.__setattr__(self, "title", normalize_text(self.title) or "")
        object.__setattr__(self, "artist", normalize_text(self.artist) or "")
        object.__setattr__(self, "album", normalize_text(self.album))
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("duration must not be negative")
        if not self.version_markers:
            _, _, markers = normalize_title_artist(self.title, self.artist)
            object.__setattr__(self, "version_markers", markers)

    @classmethod
    def from_track(cls, track: Track) -> CanonicalMediaIdentity:
        artist = ", ".join(item.name for item in track.artists)
        return cls(
            title=track.title,
            artist=artist,
            album=track.album.title if track.album else None,
            duration_ms=track.duration_ms,
            isrc=track.isrc,
        )

    @classmethod
    def from_values(
        cls,
        *,
        title: str,
        artist: str,
        album: str | None = None,
        isrc: str | None = None,
        duration_ms: int | None = None,
    ) -> CanonicalMediaIdentity:
        _, _, markers = normalize_title_artist(title, artist)
        return cls(
            title=title,
            artist=artist,
            album=album,
            isrc=isrc,
            duration_ms=duration_ms,
            version_markers=markers,
        )


@dataclass(frozen=True, slots=True)
class MediaMatch:
    score: float
    method: MatchMethod
    reasons: tuple[str, ...] = ()
    automatic_fallback_eligible: bool = False


@dataclass(frozen=True, slots=True)
class ProviderCandidate:
    provider: MusicProviderName
    provider_media_id: str
    media_identity: CanonicalMediaIdentity
    match: MediaMatch
    media_capabilities: MediaCapabilities
    source_reference: str | None = None

    def __post_init__(self) -> None:
        if not self.provider_media_id.strip():
            raise ValueError("provider media ID is required")


DURATION_TOLERANCE_MS = 3_000
AUTO_MATCH_STRONG_SCORE = 0.85


def match_media(source: CanonicalMediaIdentity, candidate: CanonicalMediaIdentity) -> MediaMatch:
    reasons: list[str] = []
    if source.media_type is not candidate.media_type:
        return MediaMatch(0.0, MatchMethod.REJECTED, ("media_type_mismatch",), False)
    exact_isrc = source.isrc is not None and source.isrc == candidate.isrc
    if source.isrc and candidate.isrc and source.isrc != candidate.isrc:
        return MediaMatch(0.0, MatchMethod.REJECTED, ("isrc_conflict",), False)
    source_base_title, _, _ = normalize_title_artist(source.title, source.artist)
    candidate_base_title, _, _ = normalize_title_artist(candidate.title, candidate.artist)
    title_equal = source_base_title == candidate_base_title
    artist_equal = source.artist == candidate.artist
    if not title_equal:
        reasons.append("title_mismatch")
    if not artist_equal:
        reasons.append("artist_mismatch")
    version_mismatch = source.version_markers != candidate.version_markers
    if version_mismatch and not exact_isrc:
        return MediaMatch(0.0, MatchMethod.REJECTED, tuple(reasons + ["version_mismatch"]), False)
    if version_mismatch:
        reasons.append("version_metadata_mismatch_proven_by_isrc")
    duration_ok = (
        source.duration_ms is None
        or candidate.duration_ms is None
        or abs(source.duration_ms - candidate.duration_ms) <= DURATION_TOLERANCE_MS
    )
    if not duration_ok and not exact_isrc:
        return MediaMatch(0.0, MatchMethod.REJECTED, tuple(reasons + ["duration_mismatch"]), False)
    if source.duration_ms is not None and candidate.duration_ms is not None:
        reasons.append("duration_match" if duration_ok else "duration_mismatch_proven_by_isrc")
    if source.album and candidate.album and source.album == candidate.album:
        reasons.append("album_match")
    if exact_isrc:
        if not title_equal or not artist_equal:
            return MediaMatch(
                0.0, MatchMethod.REJECTED, tuple(reasons + ["metadata_conflict"]), False
            )
        return MediaMatch(1.0, MatchMethod.ISRC_EXACT, tuple(["isrc_exact", *reasons]), True)
    if not title_equal or not artist_equal:
        return MediaMatch(0.3, MatchMethod.WEAK, tuple(reasons), False)
    if source.duration_ms is not None and candidate.duration_ms is not None and not duration_ok:
        return MediaMatch(0.0, MatchMethod.REJECTED, tuple(reasons), False)
    score = 0.95 if source.duration_ms is not None and candidate.duration_ms is not None else 0.80
    method = (
        MatchMethod.METADATA_STRONG
        if score >= AUTO_MATCH_STRONG_SCORE
        else MatchMethod.METADATA_MEDIUM
    )
    eligible = method is MatchMethod.METADATA_STRONG and not version_mismatch
    return MediaMatch(score, method, tuple(reasons or ["title_artist_match"]), eligible)


def candidate_supports_profile(
    candidate: ProviderCandidate, profile: EffectiveDownloadProfile, *, exact_replay: bool = False
) -> bool:
    qualities = set(candidate.media_capabilities.qualities)
    if not qualities:
        if candidate.media_capabilities.supports_lossless:
            qualities.add(QualityPreference.LOSSLESS)
        if (
            candidate.media_capabilities.supports_lossy
            or candidate.media_capabilities.native_codecs
        ):
            qualities.update((QualityPreference.HIGH, QualityPreference.STANDARD))
    effective = profile.effective_quality
    if effective is QualityPreference.LOSSLESS and QualityPreference.LOSSLESS not in qualities:
        return False
    if (
        exact_replay
        and effective is not QualityPreference.BEST_AVAILABLE
        and effective not in qualities
    ):
        return False
    return True


class ProviderCandidateRanker:
    """Pure, deterministic ordering after safety and capability filtering."""

    def __init__(self, provider_priority: Iterable[MusicProviderName] = ()) -> None:
        self._priority = {provider: index for index, provider in enumerate(provider_priority)}

    def rank(
        self,
        candidates: Iterable[ProviderCandidate],
        *,
        source_provider: MusicProviderName | None = None,
        profile: EffectiveDownloadProfile | None = None,
        exact_replay: bool = False,
        healthy_providers: set[MusicProviderName] | None = None,
    ) -> tuple[ProviderCandidate, ...]:
        healthy = healthy_providers
        eligible = [
            c
            for c in candidates
            if c.match.automatic_fallback_eligible
            and (
                profile is None or candidate_supports_profile(c, profile, exact_replay=exact_replay)
            )
            and (healthy is None or c.provider in healthy)
        ]
        return tuple(
            sorted(
                eligible,
                key=lambda c: (
                    -c.match.score,
                    0 if source_provider is not None and c.provider is source_provider else 1,
                    0 if healthy is None or c.provider in healthy else 1,
                    self._priority.get(c.provider, 10_000),
                    c.provider.value,
                    c.provider_media_id,
                ),
            )
        )


def canonical_identity_from_track(track: Track) -> CanonicalMediaIdentity:
    return CanonicalMediaIdentity.from_track(track)


def canonical_identity_from_values(**values: object) -> CanonicalMediaIdentity:
    return CanonicalMediaIdentity.from_values(**values)  # type: ignore[arg-type]


def match_candidates(
    source: CanonicalMediaIdentity, candidates: Iterable[CanonicalMediaIdentity]
) -> tuple[MediaMatch, ...]:
    return tuple(match_media(source, candidate) for candidate in candidates)


__all__ = [
    "AUTO_MATCH_STRONG_SCORE",
    "CanonicalMediaIdentity",
    "DURATION_TOLERANCE_MS",
    "MatchMethod",
    "MediaMatch",
    "MediaType",
    "ProviderCandidate",
    "ProviderCandidateRanker",
    "candidate_supports_profile",
    "canonical_identity_from_track",
    "canonical_identity_from_values",
    "match_candidates",
    "match_media",
]
