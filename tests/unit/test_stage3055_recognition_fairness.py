"""Stage 30.5.5 regressions for bounded, recording-aware enrichment fairness."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.core.enums import MusicProviderName
from app.core.models import NormalizedTrackMetadata
from app.core.recognition import RecognitionDecision, RecognitionRequest, TrackCandidate
from app.core.search import Artist, Track
from app.services.track_recognition import (
    RuleBasedRecognitionEngine,
    SimilarityAggregator,
    SimilarityWeights,
    TitleSimilarityScorer,
    TrackRecognitionService,
    _select_enrichment_finalists,
)

_PROVIDER_ORDERS = (
    (
        MusicProviderName.SPOTIFY,
        MusicProviderName.DEEZER,
        MusicProviderName.TIDAL,
        MusicProviderName.YOUTUBE_MUSIC,
        MusicProviderName.QOBUZ,
        MusicProviderName.APPLE_MUSIC,
    ),
    (
        MusicProviderName.APPLE_MUSIC,
        MusicProviderName.QOBUZ,
        MusicProviderName.YOUTUBE_MUSIC,
        MusicProviderName.TIDAL,
        MusicProviderName.DEEZER,
        MusicProviderName.SPOTIFY,
    ),
    (
        MusicProviderName.YOUTUBE_MUSIC,
        MusicProviderName.APPLE_MUSIC,
        MusicProviderName.SPOTIFY,
        MusicProviderName.QOBUZ,
        MusicProviderName.DEEZER,
        MusicProviderName.TIDAL,
    ),
    (
        MusicProviderName.SPOTIFY,
        MusicProviderName.DEEZER,
        MusicProviderName.TIDAL,
        MusicProviderName.QOBUZ,
        MusicProviderName.APPLE_MUSIC,
        MusicProviderName.YOUTUBE_MUSIC,
    ),
)


def _candidate(
    identifier: str,
    *,
    isrc: str | None,
    provider: MusicProviderName = MusicProviderName.SPOTIFY,
    title: str = "Song",
    duration: int | None = None,
) -> TrackCandidate:
    return TrackCandidate(
        Track(
            f"search:{provider.value}:{identifier}",
            title,
            (Artist("Artist"),),
            provider,
            identifier,
            isrc=isrc,
            duration_ms=duration,
        ),
        provider.value,
    )


class _AlbumScorer(TitleSimilarityScorer):
    def score(self, request: RecognitionRequest, candidate: TrackCandidate) -> float:
        return 1.0 if candidate.track.album is not None else 0.9


class _ConstantScorer(TitleSimilarityScorer):
    def score(self, request: RecognitionRequest, candidate: TrackCandidate) -> float:
        return 1.0


@dataclass
class _MetadataProvider:
    calls: list[str]
    rich_identifiers: set[str] = field(default_factory=set)
    fail_identifiers: set[str] = field(default_factory=set)
    metadata_titles: dict[str, str] = field(default_factory=dict)

    async def get_track_metadata(
        self, provider: MusicProviderName, provider_track_id: str
    ) -> NormalizedTrackMetadata:
        self.calls.append(provider_track_id)
        if provider_track_id in self.fail_identifiers:
            raise RuntimeError("provider metadata unavailable")
        if provider_track_id in self.rich_identifiers:
            return NormalizedTrackMetadata(
                provider,
                provider_track_id,
                None,
                "Song",
                "Artist",
                "Album",
                "USBBB1234567",
                180_000,
            )
        title = self.metadata_titles.get(provider_track_id, "Song")
        return NormalizedTrackMetadata(
            provider,
            provider_track_id,
            None,
            title,
            "Artist",
            None,
            "USAAA1234567" if provider_track_id in self.metadata_titles else None,
            180_000 if provider_track_id in self.metadata_titles else None,
        )


def _service(
    provider: _MetadataProvider, scorer: TitleSimilarityScorer | None = None
) -> TrackRecognitionService:
    scorer = scorer or _AlbumScorer()
    engine = RuleBasedRecognitionEngine(
        title_scorer=scorer,
        aggregator=SimilarityAggregator(
            SimilarityWeights(title=1.0, artist=0, duration=0, album=0)
        ),
    )
    return TrackRecognitionService(engine, provider)


def _request(candidates: tuple[TrackCandidate, ...]) -> RecognitionRequest:
    return RecognitionRequest("Artist Song", candidates, requested_title="Song")


def _audit_rows(order: tuple[MusicProviderName, ...]) -> tuple[TrackCandidate, ...]:
    return tuple(
        _candidate(
            "b" if provider is MusicProviderName.APPLE_MUSIC else f"a-{provider.value}",
            isrc="USBBB1234567" if provider is MusicProviderName.APPLE_MUSIC else "USAAA1234567",
            provider=provider,
        )
        for provider in order
    )


@pytest.mark.parametrize("provider_order", _PROVIDER_ORDERS)
async def test_enrichment_pressure_defect_is_fair_across_provider_orders(
    provider_order: tuple[MusicProviderName, ...],
) -> None:
    provider = _MetadataProvider([], rich_identifiers={"b"})
    result = await _service(provider).recognize_enriched(_request(_audit_rows(provider_order)))
    assert len(provider.calls) == 5
    assert "b" in provider.calls
    assert result.candidate is not None and result.candidate.track.isrc == "USBBB1234567"
    assert result.decision is RecognitionDecision.ACCEPT


@pytest.mark.parametrize("provider_order", _PROVIDER_ORDERS)
async def test_distinct_recording_ambiguity_is_stable_across_provider_orders(
    provider_order: tuple[MusicProviderName, ...],
) -> None:
    candidates = tuple(
        _candidate(
            "a" if index % 2 == 0 else "b",
            isrc="USAAA1234567" if index % 2 == 0 else "USBBB1234567",
            provider=provider,
            title="Song A" if index % 2 == 0 else "Song B",
        )
        for index, provider in enumerate(provider_order)
    )
    result = await _service(_MetadataProvider([]), _ConstantScorer()).recognize_enriched(
        _request(candidates)
    )
    assert result.decision is RecognitionDecision.ASK_USER
    assert result.runner_up_score == pytest.approx(1.0)


@pytest.mark.parametrize("provider_order", _PROVIDER_ORDERS)
async def test_same_recording_representative_may_vary_without_semantic_difference(
    provider_order: tuple[MusicProviderName, ...],
) -> None:
    candidates = tuple(
        _candidate(provider.value, isrc="USAAA1234567", provider=provider, duration=180_000)
        for provider in provider_order
    )
    result = await _service(_MetadataProvider([]), _ConstantScorer()).recognize_enriched(
        _request(candidates)
    )
    assert result.decision is RecognitionDecision.ACCEPT
    assert result.alternatives == ()
    assert result.candidate is not None and result.candidate.track.isrc == "USAAA1234567"


@pytest.mark.parametrize("provider_order", _PROVIDER_ORDERS)
async def test_variants_remain_distinct_across_provider_orders(
    provider_order: tuple[MusicProviderName, ...],
) -> None:
    variants = ("Song", "Song (Remaster)", "Song (Live)")
    candidates = tuple(
        _candidate(
            f"variant-{index}",
            isrc="USAAA1234567",
            provider=provider,
            title=variants[index % 3],
            duration=180_000,
        )
        for index, provider in enumerate(provider_order)
    )
    titles = {f"variant-{index}": variants[index % 3] for index in range(6)}
    result = await _service(
        _MetadataProvider([], metadata_titles=titles), _ConstantScorer()
    ).recognize_enriched(_request(candidates))
    assert result.decision is RecognitionDecision.ASK_USER
    assert result.candidate is not None and len(result.alternatives) == 2


def test_provisional_selection_diversifies_then_fills_spare_capacity() -> None:
    engine = RuleBasedRecognitionEngine(
        title_scorer=_ConstantScorer(),
        aggregator=SimilarityAggregator(
            SimilarityWeights(title=1.0, artist=0, duration=0, album=0)
        ),
    )
    candidates = tuple(
        [
            *(_candidate(f"a{index}", isrc="USAAA1234567") for index in range(5)),
            *(_candidate(f"b{index}", isrc="USBBB1234567") for index in range(5)),
        ]
    )
    finalists = _select_enrichment_finalists(engine.rank(_request(candidates)), limit=5)
    assert [candidate.track.provider_track_id for candidate in finalists] == [
        "a0",
        "b0",
        "a1",
        "a2",
        "a3",
    ]


@pytest.mark.parametrize("candidate_count", (0, 1, 4, 5, 9))
async def test_enrichment_attempts_never_exceed_five(candidate_count: int) -> None:
    candidates = tuple(
        _candidate(str(index), isrc=f"USABC12345{index:02}") for index in range(candidate_count)
    )
    provider = _MetadataProvider([])
    await _service(provider, _ConstantScorer()).recognize_enriched(_request(candidates))
    assert len(provider.calls) == min(candidate_count, 5)


def test_incomplete_or_conflicting_evidence_does_not_unsafe_group_finalists() -> None:
    engine = RuleBasedRecognitionEngine(
        title_scorer=_ConstantScorer(),
        aggregator=SimilarityAggregator(
            SimilarityWeights(title=1.0, artist=0, duration=0, album=0)
        ),
    )
    candidates = (
        _candidate("incomplete-a", isrc=None),
        _candidate("incomplete-b", isrc=None),
        _candidate("isrc-conflict", isrc="USCCC1234567"),
        _candidate("remaster", isrc="USAAA1234567", title="Song (Remaster)"),
    )
    finalists = _select_enrichment_finalists(engine.rank(_request(candidates)), limit=5)
    assert [candidate.track.provider_track_id for candidate in finalists] == [
        "incomplete-a",
        "incomplete-b",
        "isrc-conflict",
        "remaster",
    ]


async def test_metadata_failure_is_isolated_without_expanding_the_bound() -> None:
    candidates = tuple(_candidate(str(index), isrc=f"USABC12345{index:02}") for index in range(7))
    provider = _MetadataProvider([], fail_identifiers={"0"})
    result = await _service(provider, _ConstantScorer()).recognize_enriched(_request(candidates))
    assert len(provider.calls) == 5
    assert result.decision is RecognitionDecision.ASK_USER
