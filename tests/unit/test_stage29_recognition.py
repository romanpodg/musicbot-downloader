from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import SimpleNamespace

import pytest

from app.application.download import DownloadService, DownloadTrackUseCase
from app.core.download import DownloadOptions
from app.core.enums import (
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    ProviderResolutionStatus,
    ProviderRuntimeStatus,
    QualityProfile,
)
from app.core.models import (
    DownloadProviderCandidate,
    NativeMediaInfo,
    NormalizedTrackMetadata,
    ProviderResolutionResult,
)
from app.core.recognition import (
    RankedTrackCandidate,
    RecognitionDecision,
    RecognitionReason,
    RecognitionRequest,
    RecognitionResult,
    SimilarityScores,
    TrackCandidate,
)
from app.core.search import Artist, Track
from app.core.telegram_context import TelegramChatType, TelegramContext
from app.providers.onthespot.capabilities import ONTHESPOT_CAPABILITIES
from app.services.quality_resolution import QualityResolver
from app.services.recognized_track_resolution import RecognizedTrackResolutionAdapter
from app.services.track_recognition import (
    AmbiguityAwareDecisionPolicy,
    RuleBasedRecognitionEngine,
    SimilarityAggregator,
    SimilarityWeights,
    TitleSimilarityScorer,
    TrackRecognitionService,
)
from app.telegram.download_callbacks import (
    DownloadCallbackAction,
    encode_download_callback,
    parse_download_callback,
)


def _candidate(
    identifier: str, title: str, *, provider=MusicProviderName.SPOTIFY, isrc=None, duration=180_000
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


def test_stage29_policy_uses_distinct_runner_up_margin() -> None:
    policy = AmbiguityAwareDecisionPolicy()
    decision, reason, margin = policy.resolve(0.95, runner_up_score=0.88, variant_ambiguous=False)
    assert decision is RecognitionDecision.ASK_USER
    assert reason is RecognitionReason.CLOSE_DISTINCT_RUNNER_UP
    assert margin == pytest.approx(0.07)
    assert (
        policy.resolve(0.95, runner_up_score=0.87, variant_ambiguous=False)[0]
        is RecognitionDecision.ACCEPT
    )


def test_stage29_empty_results_are_rejected_with_a_sanitized_reason() -> None:
    result = RuleBasedRecognitionEngine().recognize(RecognitionRequest("missing", ()))
    assert result.decision is RecognitionDecision.REJECT
    assert result.reason is RecognitionReason.NO_CANDIDATE


def test_stage29_known_explicit_mismatch_cannot_automatically_accept() -> None:
    candidate = _candidate("clean", "Song")
    candidate = TrackCandidate(replace(candidate.track, explicit=False), candidate.source)
    result = RuleBasedRecognitionEngine().recognize(
        RecognitionRequest("Artist Song", (candidate,), expected_explicit=True)
    )
    assert result.decision is RecognitionDecision.ASK_USER


def test_stage29_same_isrc_provider_copies_are_one_ephemeral_recording() -> None:
    candidates = (
        _candidate("spotify-1", "Song", isrc="USABC1234567"),
        _candidate("tidal-1", "Song", provider=MusicProviderName.TIDAL, isrc="USABC1234567"),
    )
    result = RuleBasedRecognitionEngine().recognize(RecognitionRequest("Artist Song", candidates))
    assert result.decision is RecognitionDecision.ACCEPT
    assert result.runner_up_score is None
    assert result.alternatives == ()


@pytest.mark.parametrize(
    ("query", "title"), [("Artist Song live", "Song"), ("Artist Song", "Song (Live)")]
)
def test_stage29_recording_variant_mismatch_never_automatically_accepts(
    query: str, title: str
) -> None:
    result = RuleBasedRecognitionEngine().recognize(
        RecognitionRequest(query, (_candidate("song", title),))
    )
    assert result.decision is not RecognitionDecision.ACCEPT


def test_stage29_matching_variant_can_accept() -> None:
    candidate = _candidate("song-live", "Song (Live)")
    result = RuleBasedRecognitionEngine().recognize(
        RecognitionRequest("Artist Song live", (candidate,))
    )
    assert result.decision is RecognitionDecision.ACCEPT


class _Scorer(TitleSimilarityScorer):
    def __init__(self, values: dict[str, float]) -> None:
        self.values = values

    def score(self, request: RecognitionRequest, candidate: TrackCandidate) -> float:
        return self.values[candidate.track.provider_track_id]


def test_stage29_ties_are_stable_and_provider_neutral() -> None:
    first = _candidate("first", "Song")
    second = _candidate("second", "Other", provider=MusicProviderName.TIDAL)
    engine = RuleBasedRecognitionEngine(
        title_scorer=_Scorer({"first": 0.9, "second": 0.9}),
        aggregator=SimilarityAggregator(
            SimilarityWeights(title=1.0, artist=0, duration=0, album=0)
        ),
    )
    result = engine.recognize(RecognitionRequest("Song", (first, second), requested_title="Song"))
    assert result.candidate is first
    assert result.decision is RecognitionDecision.ASK_USER


@dataclass
class _MetadataProvider:
    calls: list[str]
    fail: set[str] = field(default_factory=set)

    async def get_track_metadata(
        self, provider: MusicProviderName, provider_track_id: str
    ) -> NormalizedTrackMetadata:
        self.calls.append(provider_track_id)
        if self.fail and provider_track_id in self.fail:
            raise RuntimeError("sensitive provider failure")
        return NormalizedTrackMetadata(
            provider,
            provider_track_id,
            None,
            "Song",
            "Artist",
            "Album",
            "USABC1234567",
            180_000,
            explicit=False,
        )


async def test_stage29_enrichment_is_bounded_and_failure_isolated() -> None:
    candidates = tuple(_candidate(str(i), "Song") for i in range(7))
    provider = _MetadataProvider([], {"0"})
    service = TrackRecognitionService(RuleBasedRecognitionEngine(), provider)
    result = await service.recognize_enriched(RecognitionRequest("Artist Song", candidates))
    assert len(provider.calls) == 5
    assert result.candidate is not None
    assert result.decision is RecognitionDecision.ACCEPT


async def test_stage29_confirmation_correction_and_retry_are_ephemeral() -> None:
    class Resolver:
        async def resolve_track_id(self, track: Track) -> int:
            raise AssertionError("canonical resolution must not run")

    class Submission:
        async def submit(self, request, *, canonical_track_id, target):  # type: ignore[no-untyped-def]
            raise AssertionError("durable submission must not run")

    selected = _candidate("selected", "Song")
    alternative = _candidate("alternative", "Other")
    service = DownloadService(
        DownloadTrackUseCase(Resolver(), Submission()), token_factory=lambda: "a" * 24
    )
    confirmation = service.create_confirmation(
        context=TelegramContext(7, 7, TelegramChatType.PRIVATE),
        result=RecognitionResult(
            selected,
            0.95,
            RecognitionDecision.ACCEPT,
            (RankedTrackCandidate(alternative, SimilarityScores(0.8, 0.8, 0.5, 0.5), 0.8),),
        ),
        options=DownloadOptions(),
    )
    assert confirmation is not None and confirmation.presentation_alternatives == ()
    assert (
        parse_download_callback(
            encode_download_callback(DownloadCallbackAction.EXPAND, confirmation.token)
        )
        is not None
    )
    assert (
        service.expand_alternatives(
            context=TelegramContext(9, 9, TelegramChatType.PRIVATE), token=confirmation.token
        )
        is None
    )
    expanded = service.expand_alternatives(context=confirmation.context, token=confirmation.token)
    assert expanded is not None and expanded.presentation_alternatives == (alternative.track,)
    assert service.cancel(context=confirmation.context, token=confirmation.token)
    assert (
        service.expand_alternatives(context=confirmation.context, token=confirmation.token) is None
    )


async def test_stage29_spotify_recognition_does_not_pin_lossless_download_provider() -> None:
    class CanonicalResolver:
        calls: list[tuple[MusicProviderName, str, bool]] = []

        async def resolve_provider_track(
            self, provider: MusicProviderName, provider_track_id: str, *, discover: bool
        ) -> SimpleNamespace:
            self.calls.append((provider, provider_track_id, discover))
            return SimpleNamespace(track=SimpleNamespace(id=29))

    class ProviderResolver:
        async def resolve(self, track_id: int) -> ProviderResolutionResult:
            assert track_id == 29
            return ProviderResolutionResult(
                29,
                ProviderResolutionStatus.AVAILABLE,
                (
                    DownloadProviderCandidate(
                        29,
                        1,
                        MusicProviderName.QOBUZ,
                        "qobuz-recording",
                        ProviderRuntimeStatus.AVAILABLE,
                        ONTHESPOT_CAPABILITIES[MusicProviderName.QOBUZ],
                        NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
                    ),
                ),
                (),
            )

    spotify = _candidate("spotify-recording", "Song")
    canonical = CanonicalResolver()
    track_id = await RecognizedTrackResolutionAdapter(canonical).resolve_track_id(spotify.track)
    result = await QualityResolver(ProviderResolver()).resolve(track_id, QualityProfile.LOSSLESS)
    assert canonical.calls == [(MusicProviderName.SPOTIFY, "spotify-recording", True)]
    assert result.primary_plan is not None
    assert result.primary_plan.provider is MusicProviderName.QOBUZ
