"""Stage 15 application use case for provider-neutral track search."""

from __future__ import annotations

from app.core.search import TrackSearchRequest, TrackSearchResult
from app.services.track_search import TrackSearchService


class SearchTracksUseCase:
    """Validate user search intent and delegate provider orchestration to the service."""

    def __init__(self, search: TrackSearchService) -> None:
        self._search = search

    async def execute(self, request: TrackSearchRequest) -> TrackSearchResult:
        return await self._search.search(request)
