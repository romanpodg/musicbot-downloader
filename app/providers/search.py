"""Provider adapter contract for Stage 15 catalog search."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.enums import MusicProviderName
from app.core.search import Track, TrackSearchRequest


class TrackSearchProvider(ABC):
    """A provider-isolated adapter that returns only normalized search tracks."""

    @property
    @abstractmethod
    def provider(self) -> MusicProviderName:
        """Provider identity registered for this adapter."""

    @abstractmethod
    async def search(self, request: TrackSearchRequest) -> tuple[Track, ...]:
        """Search this provider without receiving Telegram, storage, or download concerns."""
