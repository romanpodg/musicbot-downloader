"""Stage 15 search and Stage 17 recognition application flow."""

from __future__ import annotations

from app.core.recognition import RecognitionRequest, RecognitionResult, TrackCandidate
from app.core.search import TrackSearchRequest, TrackSearchResult
from app.services.track_recognition import TrackRecognitionService
from app.services.track_search import TrackSearchService


class SearchTracksUseCase:
    """Search normalized catalogs and optionally recognize the leading intended track."""

    def __init__(
        self,
        search: TrackSearchService,
        recognition: TrackRecognitionService | None = None,
    ) -> None:
        self._search = search
        self._recognition = recognition

    async def execute(self, request: TrackSearchRequest) -> TrackSearchResult:
        return await self._search.search(request)

    async def recognize(self, request: TrackSearchRequest) -> RecognitionResult:
        """Run Stage 17 after the unchanged Stage 15 search-result boundary."""

        search_result = await self.execute(request)
        if self._recognition is None:
            raise RuntimeError("track recognition service is not composed")
        candidates = tuple(
            TrackCandidate(track=track, source=track.provider.value)
            for track in search_result.tracks
        )
        return self._recognition.recognize(
            RecognitionRequest(query=search_result.query, candidates=candidates)
        )
