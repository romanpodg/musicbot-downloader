"""Narrow provider contract required by the current metadata stage."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.core.enums import MusicProviderName
from app.core.models import NormalizedTrackMetadata, TrackSearchCandidate, TrackSearchRequest


@dataclass(frozen=True, slots=True)
class TrackReference:
    provider: MusicProviderName
    provider_track_id: str
    source_url: str


@dataclass(frozen=True, slots=True)
class ProviderAvailability:
    available: bool
    version: str | None = None
    detail: str | None = None


class MusicProvider(ABC):
    @abstractmethod
    async def availability(self) -> ProviderAvailability:
        """Report whether the adapter dependency can be initialized."""

    @abstractmethod
    def detect_url(self, url: str) -> TrackReference:
        """Validate a single-track URL and return its provider identity."""

    @abstractmethod
    async def get_metadata(self, url: str) -> NormalizedTrackMetadata:
        """Resolve and normalize metadata for one track URL."""

    @abstractmethod
    async def list_searchable_providers(self) -> tuple[MusicProviderName, ...]:
        """Return providers whose initialized accounts can currently search."""

    @abstractmethod
    async def search_tracks(self, request: TrackSearchRequest) -> list[TrackSearchCandidate]:
        """Return a bounded set of lightweight track candidates."""

    async def close(self) -> None:
        """Release provider-owned resources, if any."""
        return None
