from __future__ import annotations

from app.application.search import SearchTracksUseCase
from app.core.enums import MusicProviderName
from app.core.recognition import RecognitionDecision
from app.core.search import Artist, Track, TrackSearchRequest
from app.providers.search import TrackSearchProvider
from app.services.track_recognition import RuleBasedRecognitionEngine, TrackRecognitionService
from app.services.track_search import TrackSearchProviderRegistry, TrackSearchService


class _CatalogProvider(TrackSearchProvider):
    @property
    def provider(self) -> MusicProviderName:
        return MusicProviderName.SPOTIFY

    async def search(self, request: TrackSearchRequest) -> tuple[Track, ...]:
        return (
            Track(
                id="search:spotify:around-the-world",
                title="Around The World",
                artists=(Artist("Daft Punk"),),
                provider=MusicProviderName.SPOTIFY,
                provider_track_id="around-the-world",
            ),
            Track(
                id="search:spotify:one-more-time",
                title="One More Time",
                artists=(Artist("Daft Punk"),),
                provider=MusicProviderName.SPOTIFY,
                provider_track_id="one-more-time",
            ),
        )


async def test_stage17_search_result_flows_into_provider_independent_recognition() -> None:
    use_case = SearchTracksUseCase(
        TrackSearchService(TrackSearchProviderRegistry((_CatalogProvider(),))),
        TrackRecognitionService(RuleBasedRecognitionEngine()),
    )

    result = await use_case.recognize(TrackSearchRequest("Daft Punk One More Time"))

    assert result.candidate is not None
    assert result.candidate.track.provider is MusicProviderName.SPOTIFY
    assert result.candidate.track.title == "One More Time"
    assert result.decision is RecognitionDecision.ACCEPT
    assert [item.candidate.track.title for item in result.alternatives] == ["Around The World"]
