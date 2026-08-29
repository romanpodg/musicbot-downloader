"""Deterministic, provider-independent Stage 17 track recognition."""

from __future__ import annotations

import re
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.core.recognition import (
    RankedTrackCandidate,
    RecognitionDecision,
    RecognitionRequest,
    RecognitionResult,
    SimilarityScores,
    TrackCandidate,
)

_NON_ALPHANUMERIC = re.compile(r"[^\w]+", re.UNICODE)
_DEFAULT_NEUTRAL_SCORE = 0.5
_DEFAULT_DURATION_TOLERANCE_MS = 30_000


class RecognitionEngine(ABC):
    """Replaceable recognition boundary with no transport or provider dependency."""

    @abstractmethod
    def recognize(self, request: RecognitionRequest) -> RecognitionResult:
        """Rank request candidates and return a decision-ready result."""


class TitleSimilarityScorer:
    """Score a candidate title against explicit title metadata or the user query."""

    def score(self, request: RecognitionRequest, candidate: TrackCandidate) -> float:
        if request.requested_title is not None:
            return _text_similarity(request.requested_title, candidate.track.title)
        return _text_similarity_with_context(request.query, candidate.track.title)


class ArtistSimilarityScorer:
    """Score the strongest requested/candidate artist pairing."""

    def score(self, request: RecognitionRequest, candidate: TrackCandidate) -> float:
        if request.requested_artists:
            requested = request.requested_artists
            return max(
                _text_similarity(expected_artist, candidate_artist.name)
                for expected_artist in requested
                for candidate_artist in candidate.track.artists
            )
        requested = (request.query,)
        return max(
            _text_similarity_with_context(expected_artist, candidate_artist.name)
            for expected_artist in requested
            for candidate_artist in candidate.track.artists
        )


class DurationSimilarityScorer:
    """Score duration proximity, keeping unknown duration deliberately neutral."""

    def __init__(self, *, tolerance_ms: int = _DEFAULT_DURATION_TOLERANCE_MS) -> None:
        if tolerance_ms <= 0:
            raise ValueError("duration tolerance must be positive")
        self._tolerance_ms = tolerance_ms

    def score(self, request: RecognitionRequest, candidate: TrackCandidate) -> float:
        expected = request.expected_duration_ms
        actual = candidate.track.duration_ms
        if expected is None or actual is None:
            return _DEFAULT_NEUTRAL_SCORE
        return max(0.0, 1.0 - (abs(expected - actual) / self._tolerance_ms))


class AlbumSimilarityScorer:
    """Score album metadata when supplied, keeping omitted data neutral."""

    def score(self, request: RecognitionRequest, candidate: TrackCandidate) -> float:
        if request.expected_album is None or candidate.track.album is None:
            return _DEFAULT_NEUTRAL_SCORE
        return _text_similarity(request.expected_album, candidate.track.album.title)


@dataclass(frozen=True, slots=True)
class SimilarityWeights:
    """Configurable component weights; aggregation normalizes their total."""

    title: float = 0.45
    artist: float = 0.35
    duration: float = 0.15
    album: float = 0.05

    def __post_init__(self) -> None:
        if any(value < 0.0 for value in (self.title, self.artist, self.duration, self.album)):
            raise ValueError("similarity weights must not be negative")
        if self.total == 0.0:
            raise ValueError("at least one similarity weight must be positive")

    @property
    def total(self) -> float:
        return self.title + self.artist + self.duration + self.album


class SimilarityAggregator:
    """Combine independent scores without knowing how those scores were produced."""

    def __init__(self, weights: SimilarityWeights | None = None) -> None:
        self._weights = weights or SimilarityWeights()

    def aggregate(self, scores: SimilarityScores) -> float:
        weighted_sum = (
            scores.title * self._weights.title
            + scores.artist * self._weights.artist
            + scores.duration * self._weights.duration
            + scores.album * self._weights.album
        )
        return weighted_sum / self._weights.total


class RecognitionRanker:
    """Order already-scored candidates without recalculating their similarity."""

    def rank(self, candidates: Iterable[RankedTrackCandidate]) -> tuple[RankedTrackCandidate, ...]:
        return tuple(sorted(candidates, key=lambda item: -item.confidence))


@dataclass(frozen=True, slots=True)
class ConfidenceThresholds:
    """Configurable recognition decision thresholds."""

    accept: float = 0.90
    ask_user: float = 0.60

    def __post_init__(self) -> None:
        if not 0.0 <= self.ask_user <= self.accept <= 1.0:
            raise ValueError("confidence thresholds must satisfy 0 <= ask_user <= accept <= 1")


class ConfidenceResolver:
    """Translate aggregate confidence into a stable Stage 17 decision."""

    def __init__(self, thresholds: ConfidenceThresholds | None = None) -> None:
        self._thresholds = thresholds or ConfidenceThresholds()

    def resolve(self, confidence: float) -> RecognitionDecision:
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("recognition confidence must be between 0.0 and 1.0")
        if confidence >= self._thresholds.accept:
            return RecognitionDecision.ACCEPT
        if confidence >= self._thresholds.ask_user:
            return RecognitionDecision.ASK_USER
        return RecognitionDecision.REJECT


class RuleBasedRecognitionEngine(RecognitionEngine):
    """Current deterministic engine assembled from independently replaceable components."""

    def __init__(
        self,
        *,
        title_scorer: TitleSimilarityScorer | None = None,
        artist_scorer: ArtistSimilarityScorer | None = None,
        duration_scorer: DurationSimilarityScorer | None = None,
        album_scorer: AlbumSimilarityScorer | None = None,
        aggregator: SimilarityAggregator | None = None,
        ranker: RecognitionRanker | None = None,
        confidence_resolver: ConfidenceResolver | None = None,
    ) -> None:
        self._title_scorer = title_scorer or TitleSimilarityScorer()
        self._artist_scorer = artist_scorer or ArtistSimilarityScorer()
        self._duration_scorer = duration_scorer or DurationSimilarityScorer()
        self._album_scorer = album_scorer or AlbumSimilarityScorer()
        self._aggregator = aggregator or SimilarityAggregator()
        self._ranker = ranker or RecognitionRanker()
        self._confidence_resolver = confidence_resolver or ConfidenceResolver()

    def recognize(self, request: RecognitionRequest) -> RecognitionResult:
        ranked = self._ranker.rank(
            self._score(request, candidate) for candidate in request.candidates
        )
        if not ranked:
            return RecognitionResult(None, 0.0, RecognitionDecision.REJECT)
        leading = ranked[0]
        return RecognitionResult(
            candidate=leading.candidate,
            confidence=leading.confidence,
            decision=self._confidence_resolver.resolve(leading.confidence),
            alternatives=ranked[1:],
        )

    def _score(
        self, request: RecognitionRequest, candidate: TrackCandidate
    ) -> RankedTrackCandidate:
        title_score = self._title_scorer.score(request, candidate)
        artist_score = self._artist_scorer.score(request, candidate)
        if request.requested_title is None and not request.requested_artists:
            title_score, artist_score = _fallback_query_scores(request.query, candidate)
        scores = SimilarityScores(
            title=title_score,
            artist=artist_score,
            duration=self._duration_scorer.score(request, candidate),
            album=self._album_scorer.score(request, candidate),
        )
        return RankedTrackCandidate(candidate, scores, self._aggregator.aggregate(scores))


class TrackRecognitionService:
    """Application service that delegates pure recognition to its injected engine."""

    def __init__(self, engine: RecognitionEngine) -> None:
        self._engine = engine

    def recognize(self, request: RecognitionRequest) -> RecognitionResult:
        return self._engine.recognize(request)


def _text_similarity(requested: str, candidate: str) -> float:
    normalized_requested = _normalize_text(requested)
    normalized_candidate = _normalize_text(candidate)
    if not normalized_requested or not normalized_candidate:
        return 0.0
    if normalized_requested == normalized_candidate:
        return 1.0
    requested_tokens = set(normalized_requested.split())
    candidate_tokens = set(normalized_candidate.split())
    shared = requested_tokens & candidate_tokens
    if not shared:
        return 0.0
    token_f1 = 2.0 * len(shared) / (len(requested_tokens) + len(candidate_tokens))
    sequence_ratio = SequenceMatcher(None, normalized_requested, normalized_candidate).ratio()
    return max(token_f1, sequence_ratio)


def _text_similarity_with_context(requested: str, candidate: str) -> float:
    """Legacy scorer behavior for direct component diagnostics.

    Recognition decisions use ``_fallback_query_scores`` below, which requires
    combined artist/title evidence. Keeping this helper preserves the Stage 17
    component scorer's useful phrase-in-query signal for explicit diagnostics.
    """

    normalized_requested = _normalize_text(requested)
    normalized_candidate = _normalize_text(candidate)
    if normalized_candidate and normalized_candidate in normalized_requested:
        return 1.0
    return _text_similarity(requested, candidate)


def _fallback_query_scores(query: str, candidate: TrackCandidate) -> tuple[float, float]:
    """Score raw queries using combined intent rather than independent victories.

    Candidate artist tokens are removed from the normalized query, then the
    remaining title intent is compared strictly. Thus ``Daft Punk One More
    Time`` + ``Daft Punk — One More Time`` is exact, while a title such as
    ``Time`` covers only part of the remaining intent and cannot reach ACCEPT.
    """

    normalized_query = _normalize_text(query)
    query_tokens = normalized_query.split()
    best_artist = 0.0
    best_title = 0.0
    for artist in candidate.track.artists:
        artist_tokens = _normalize_text(artist.name).split()
        if not artist_tokens:
            continue
        coverage = sum(token in query_tokens for token in artist_tokens) / len(artist_tokens)
        remaining_tokens = list(query_tokens)
        for token in artist_tokens:
            try:
                remaining_tokens.remove(token)
            except ValueError:
                pass
        remaining = " ".join(remaining_tokens) or normalized_query
        title_score = _text_similarity(remaining, candidate.track.title)
        pair = (title_score, coverage)
        if pair > (best_title, best_artist):
            best_title, best_artist = pair
    return best_title, best_artist


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(_NON_ALPHANUMERIC.sub(" ", normalized).split())
