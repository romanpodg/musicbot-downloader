"""Provider-independent domain models for Stage 17 track recognition."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from app.core.search import Track


class RecognitionDecision(StrEnum):
    """The next interaction required for a ranked recognition candidate."""

    ACCEPT = "ACCEPT"
    ASK_USER = "ASK_USER"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class TrackCandidate:
    """A normalized catalog track plus opaque source and optional search metadata."""

    track: Track
    source: str
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.track, Track):
            raise TypeError("candidate track must be a search Track")
        _require_text(self.source, "candidate source")
        normalized_metadata: dict[str, str] = {}
        for key, value in self.metadata.items():
            _require_text(key, "candidate metadata key")
            _require_text(value, "candidate metadata value")
            normalized_metadata[key.strip()] = value.strip()
        object.__setattr__(self, "source", self.source.strip())
        object.__setattr__(self, "metadata", MappingProxyType(normalized_metadata))


@dataclass(frozen=True, slots=True)
class RecognitionRequest:
    """User intent and catalog candidates to evaluate without provider access."""

    query: str
    candidates: tuple[TrackCandidate, ...]
    requested_title: str | None = None
    requested_artists: tuple[str, ...] = ()
    expected_duration_ms: int | None = None
    expected_album: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.query, "recognition query")
        normalized_query = self.query.strip()
        if len(normalized_query) > 512:
            raise ValueError("recognition query exceeds 512 characters")
        candidates = tuple(self.candidates)
        if any(not isinstance(candidate, TrackCandidate) for candidate in candidates):
            raise TypeError("recognition candidates must be TrackCandidate values")
        requested_title = _normalize_optional_text(self.requested_title, "requested title")
        requested_artists = tuple(artist.strip() for artist in self.requested_artists)
        if any(not artist for artist in requested_artists):
            raise ValueError("requested artists must not contain empty values")
        if self.expected_duration_ms is not None and self.expected_duration_ms < 0:
            raise ValueError("expected duration must not be negative")
        expected_album = _normalize_optional_text(self.expected_album, "expected album")
        object.__setattr__(self, "query", normalized_query)
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "requested_title", requested_title)
        object.__setattr__(self, "requested_artists", requested_artists)
        object.__setattr__(self, "expected_album", expected_album)


@dataclass(frozen=True, slots=True)
class SimilarityScores:
    """Independent component scores produced for one candidate."""

    title: float
    artist: float
    duration: float
    album: float

    def __post_init__(self) -> None:
        for name, value in (
            ("title", self.title),
            ("artist", self.artist),
            ("duration", self.duration),
            ("album", self.album),
        ):
            _require_score(value, name)


@dataclass(frozen=True, slots=True)
class RankedTrackCandidate:
    """A candidate with explainable component scores and its aggregate confidence."""

    candidate: TrackCandidate
    scores: SimilarityScores
    confidence: float

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, TrackCandidate):
            raise TypeError("ranked candidate must contain a TrackCandidate")
        _require_score(self.confidence, "candidate confidence")


@dataclass(frozen=True, slots=True)
class RecognitionResult:
    """The top ranked recognition candidate and any non-selected alternatives."""

    candidate: TrackCandidate | None
    confidence: float
    decision: RecognitionDecision
    alternatives: tuple[RankedTrackCandidate, ...] = ()

    def __post_init__(self) -> None:
        if self.candidate is not None and not isinstance(self.candidate, TrackCandidate):
            raise TypeError("recognition candidate must be a TrackCandidate")
        _require_score(self.confidence, "recognition confidence")
        alternatives = tuple(self.alternatives)
        if any(not isinstance(item, RankedTrackCandidate) for item in alternatives):
            raise TypeError("recognition alternatives must be ranked candidates")
        object.__setattr__(self, "alternatives", alternatives)

    @property
    def track(self) -> Track | None:
        """Convenience access to the leading normalized track for future presentation layers."""

        return self.candidate.track if self.candidate is not None else None


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must not be empty")


def _normalize_optional_text(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    _require_text(value, field)
    return value.strip()


def _require_score(value: float, field: str) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{field} must be between 0.0 and 1.0")
