"""Provider-specific mapping from runtime search candidates to Stage 15 tracks.

The OnTheSpot runtime intentionally returns lightweight, sanitized catalog
candidates.  These mappers are the only place Stage 16 converts those
provider-scoped candidates into the provider-neutral Stage 15 search model.
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Iterable

from app.core.enums import MusicProviderName
from app.core.models import TrackSearchCandidate
from app.core.search import Artist, Track


class ProviderTrackMapper(ABC):
    """Map candidates from exactly one provider without exposing runtime DTOs."""

    provider: MusicProviderName

    def map_all(self, candidates: Iterable[TrackSearchCandidate]) -> tuple[Track, ...]:
        tracks: list[Track] = []
        seen_ids: set[str] = set()
        for candidate in candidates:
            track = self.map(candidate)
            if track is None or track.provider_track_id in seen_ids:
                continue
            seen_ids.add(track.provider_track_id)
            tracks.append(track)
        return tuple(tracks)

    def map(self, candidate: TrackSearchCandidate) -> Track | None:
        if candidate.provider is not self.provider:
            return None
        title = _text_or_none(candidate.title)
        artist = _text_or_none(candidate.artist)
        track_id = _text_or_none(candidate.provider_track_id)
        if title is None or artist is None or track_id is None:
            return None
        return Track(
            id=f"search:{self.provider.value}:{track_id}",
            title=title,
            artists=(Artist(artist),),
            provider=self.provider,
            provider_track_id=track_id,
        )


class SpotifyTrackMapper(ProviderTrackMapper):
    provider = MusicProviderName.SPOTIFY


class DeezerTrackMapper(ProviderTrackMapper):
    provider = MusicProviderName.DEEZER


class TidalTrackMapper(ProviderTrackMapper):
    provider = MusicProviderName.TIDAL


def _text_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
