from __future__ import annotations

import pytest

from app.core.enums import MusicProviderName
from app.core.recognition import (
    RankedTrackCandidate,
    RecognitionDecision,
    RecognitionRequest,
    RecognitionResult,
    SimilarityScores,
    TrackCandidate,
)
from app.core.search import Album, Artist, Track
from app.services.track_recognition import (
    AlbumSimilarityScorer,
    ArtistSimilarityScorer,
    ConfidenceResolver,
    ConfidenceThresholds,
    DurationSimilarityScorer,
    RecognitionEngine,
    RecognitionRanker,
    RuleBasedRecognitionEngine,
    SimilarityAggregator,
    SimilarityWeights,
    TitleSimilarityScorer,
    TrackRecognitionService,
)


def _track(
    *,
    title: str = "One More Time",
    artist: str = "Daft Punk",
    album: str | None = "Discovery",
    duration_ms: int | None = 320_000,
    provider: MusicProviderName = MusicProviderName.SPOTIFY,
    provider_track_id: str = "one-more-time",
) -> Track:
    return Track(
        id=f"search:{provider.value}:{provider_track_id}",
        title=title,
        artists=(Artist(artist),),
        provider=provider,
        provider_track_id=provider_track_id,
        album=Album(album) if album is not None else None,
        duration_ms=duration_ms,
    )


def _candidate(**changes: object) -> TrackCandidate:
    values: dict[str, object] = {
        "track": _track(),
        "source": "spotify",
        "metadata": {"catalog_rank": "1"},
    }
    values.update(changes)
    return TrackCandidate(**values)  # type: ignore[arg-type]


def test_stage17_candidate_creation_keeps_source_and_metadata_outside_track() -> None:
    candidate = _candidate()

    assert candidate.track.title == "One More Time"
    assert candidate.source == "spotify"
    assert candidate.metadata == {"catalog_rank": "1"}
    with pytest.raises(TypeError):
        candidate.metadata["catalog_rank"] = "2"  # type: ignore[index]
    with pytest.raises(ValueError, match="source"):
        _candidate(source=" ")


def test_stage17_recognition_request_normalizes_and_validates_intent() -> None:
    candidate = _candidate()

    request = RecognitionRequest(
        query="  Daft Punk One More Time  ",
        candidates=(candidate,),
        requested_artists=(" Daft Punk ",),
        expected_album=" Discovery ",
    )

    assert request.query == "Daft Punk One More Time"
    assert request.requested_artists == ("Daft Punk",)
    assert request.expected_album == "Discovery"
    with pytest.raises(ValueError, match="query"):
        RecognitionRequest(" ", (candidate,))
    with pytest.raises(ValueError, match="duration"):
        RecognitionRequest("query", (candidate,), expected_duration_ms=-1)


def test_stage17_title_similarity_normalizes_case_whitespace_and_punctuation() -> None:
    request = RecognitionRequest("  daft punk: ONE   MORE time! ", (_candidate(),))

    assert TitleSimilarityScorer().score(request, _candidate()) == 1.0


def test_stage17_artist_similarity_uses_explicit_artist_information_when_available() -> None:
    candidate = _candidate()
    request = RecognitionRequest(
        "One More Time",
        (candidate,),
        requested_artists=("  DAFT    PUNK ",),
    )

    assert ArtistSimilarityScorer().score(request, candidate) == 1.0


def test_stage17_duration_similarity_is_neutral_when_metadata_is_unavailable() -> None:
    candidate = _candidate(track=_track(duration_ms=None))
    request = RecognitionRequest(
        "Daft Punk One More Time", (candidate,), expected_duration_ms=320_000
    )

    assert DurationSimilarityScorer().score(request, candidate) == 0.5
    assert (
        DurationSimilarityScorer().score(RecognitionRequest("query", (candidate,)), candidate)
        == 0.5
    )
    assert (
        DurationSimilarityScorer().score(
            RecognitionRequest("query", (_candidate(),), expected_duration_ms=320_000), _candidate()
        )
        == 1.0
    )


def test_stage17_album_similarity_is_optional_and_normalized() -> None:
    candidate = _candidate()
    request = RecognitionRequest("query", (candidate,), expected_album=" discovery ")

    assert AlbumSimilarityScorer().score(request, candidate) == 1.0
    assert (
        AlbumSimilarityScorer().score(RecognitionRequest("query", (candidate,)), candidate) == 0.5
    )
    assert (
        AlbumSimilarityScorer().score(
            RecognitionRequest(
                "query", (_candidate(track=_track(album=None)),), expected_album="Discovery"
            ),
            _candidate(track=_track(album=None)),
        )
        == 0.5
    )


def test_stage17_aggregator_uses_configured_isolated_weights() -> None:
    scores = SimilarityScores(title=0.8, artist=0.2, duration=0.1, album=0.0)

    assert (
        SimilarityAggregator(
            SimilarityWeights(title=1.0, artist=0.0, duration=0.0, album=0.0)
        ).aggregate(scores)
        == 0.8
    )


def test_stage17_ranker_orders_pre_scored_candidates_without_source_priority() -> None:
    first = _candidate(track=_track(provider_track_id="a"))
    second = _candidate(track=_track(provider_track_id="b"))
    scores = SimilarityScores(0.0, 0.0, 0.5, 0.5)

    ranked = RecognitionRanker().rank(
        (
            RankedTrackCandidate(second, scores, 0.7),
            RankedTrackCandidate(first, scores, 0.9),
        )
    )

    assert [item.candidate.track.provider_track_id for item in ranked] == ["a", "b"]


@pytest.mark.parametrize(
    ("confidence", "decision"),
    [
        (0.90, RecognitionDecision.ACCEPT),
        (0.60, RecognitionDecision.ASK_USER),
        (0.59, RecognitionDecision.REJECT),
    ],
)
def test_stage17_confidence_resolver_uses_isolated_thresholds(
    confidence: float, decision: RecognitionDecision
) -> None:
    resolver = ConfidenceResolver(ConfidenceThresholds(accept=0.90, ask_user=0.60))

    assert resolver.resolve(confidence) is decision


def test_stage17_rule_based_engine_accepts_exact_title_and_artist_without_optional_metadata() -> (
    None
):
    candidate = _candidate()

    result = RuleBasedRecognitionEngine().recognize(
        RecognitionRequest("Daft Punk One More Time", (candidate,))
    )

    assert result.candidate is candidate
    assert result.track is candidate.track
    assert result.confidence >= 0.90
    assert result.decision is RecognitionDecision.ACCEPT


@pytest.mark.parametrize("title", ["Time", "One", "More"])
def test_stage20_raw_query_partial_title_false_positive_is_not_accepted(title: str) -> None:
    candidate = _candidate(track=_track(title=title, album=None, duration_ms=None))

    result = RuleBasedRecognitionEngine().recognize(
        RecognitionRequest("Daft Punk One More Time", (candidate,))
    )

    assert result.decision is not RecognitionDecision.ACCEPT


def test_stage20_raw_query_exact_combined_intent_and_one_word_title_remain_accepted() -> None:
    exact = _candidate(track=_track(album=None, duration_ms=None))
    adele = TrackCandidate(
        _track(title="Hello", artist="Adele", album=None, duration_ms=None), "spotify"
    )

    engine = RuleBasedRecognitionEngine()
    assert (
        engine.recognize(RecognitionRequest("Daft Punk One More Time", (exact,))).decision
        is RecognitionDecision.ACCEPT
    )
    assert (
        engine.recognize(RecognitionRequest("Adele Hello", (adele,))).decision
        is RecognitionDecision.ACCEPT
    )


def test_stage17_rule_based_engine_rejects_an_unrelated_title_from_same_artist() -> None:
    candidate = _candidate(track=_track(title="Around The World", provider_track_id="around-world"))

    result = RuleBasedRecognitionEngine().recognize(
        RecognitionRequest("Daft Punk One More Time", (candidate,))
    )

    assert result.candidate is candidate
    assert result.confidence < 0.60
    assert result.decision is RecognitionDecision.REJECT


def test_stage17_rule_based_engine_exposes_ranked_alternatives_without_provider_logic() -> None:
    best = _candidate(
        track=_track(provider=MusicProviderName.DEEZER, provider_track_id="best"), source="deezer"
    )
    alternative = _candidate(
        track=_track(
            title="Around The World",
            provider=MusicProviderName.TIDAL,
            provider_track_id="alternative",
        ),
        source="tidal",
    )

    result = RuleBasedRecognitionEngine().recognize(
        RecognitionRequest("Daft Punk One More Time", (alternative, best))
    )

    assert result.candidate is best
    assert [item.candidate for item in result.alternatives] == [alternative]


class _StubEngine(RecognitionEngine):
    def __init__(self, result: RecognitionResult) -> None:
        self.result = result
        self.requests: list[RecognitionRequest] = []

    def recognize(self, request: RecognitionRequest) -> RecognitionResult:
        self.requests.append(request)
        return self.result


def test_stage17_service_delegates_only_to_its_replaceable_engine() -> None:
    candidate = _candidate()
    expected = RecognitionResult(candidate, 0.9, RecognitionDecision.ACCEPT)
    engine = _StubEngine(expected)
    request = RecognitionRequest("Daft Punk One More Time", (candidate,))

    assert TrackRecognitionService(engine).recognize(request) is expected
    assert engine.requests == [request]
