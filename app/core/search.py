"""Provider-neutral domain models for the Stage 15 track-search boundary."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.enums import MusicProviderName


@dataclass(frozen=True, slots=True)
class Artist:
    """A display artist returned by a provider catalog, not a provider DTO."""

    name: str
    identifier: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.name, "artist name")
        _validate_optional_text(self.identifier, "artist identifier")


@dataclass(frozen=True, slots=True)
class Album:
    """A display album returned by a provider catalog, not a persisted Album entity."""

    title: str
    identifier: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.title, "album title")
        _validate_optional_text(self.identifier, "album identifier")


@dataclass(frozen=True, slots=True)
class Track:
    """One normalized catalog result, deliberately separate from storage ``Track``."""

    id: str
    title: str
    artists: tuple[Artist, ...]
    provider: MusicProviderName
    provider_track_id: str
    album: Album | None = None
    duration_ms: int | None = None
    isrc: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.id, "track ID")
        _require_text(self.title, "track title")
        _require_text(self.provider_track_id, "provider track ID")
        if not self.artists:
            raise ValueError("track must contain at least one artist")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("track duration must not be negative")


@dataclass(frozen=True, slots=True)
class TrackSearchRequest:
    """Provider-independent user search intent with bounded optional targeting."""

    query: str
    providers: tuple[MusicProviderName, ...] | None = None
    limit: int = 20

    def __post_init__(self) -> None:
        normalized_query = self.query.strip()
        if not normalized_query:
            raise ValueError("search query must not be empty")
        if len(normalized_query) > 512:
            raise ValueError("search query exceeds 512 characters")
        if not 1 <= self.limit <= 50:
            raise ValueError("search limit must be between 1 and 50")
        if self.providers is not None:
            if not self.providers:
                raise ValueError("search providers must not be empty")
            if len(set(self.providers)) != len(self.providers):
                raise ValueError("search providers must be unique")
        object.__setattr__(self, "query", normalized_query)


@dataclass(frozen=True, slots=True)
class TrackSearchResult:
    """Normalized search output; cross-provider matching remains a later-stage concern."""

    query: str
    tracks: tuple[Track, ...]

    def __post_init__(self) -> None:
        _require_text(self.query, "search result query")


def _require_text(value: str, field: str) -> None:
    if not value.strip():
        raise ValueError(f"{field} must not be empty")


def _validate_optional_text(value: str | None, field: str) -> None:
    if value is not None:
        _require_text(value, field)
