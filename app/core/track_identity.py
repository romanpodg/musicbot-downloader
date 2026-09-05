"""Deterministic normalization for recording identity."""

from __future__ import annotations

import re
import unicodedata

from app.core.models import NormalizedTrackMetadata, TrackIdentity

_ISRC = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}\d{7}$")
_WHITESPACE = re.compile(r"\s+")
_FEATURE = re.compile(r"\b(?:feat(?:uring)?|ft)\.?\s+(.+)$")
_FEATURE_TOKEN = re.compile(r"\b(?:feat(?:uring)?|ft)\.?(?=\s|$)")
_BRACKETED = re.compile(r"[\(\[\{]([^\)\]\}]+)[\)\]\}]")
_DASH_SUFFIX = re.compile(r"\s+-\s+(.+)$")
_SURROUNDING_PUNCTUATION = " \t\r\n-–—_:;,.!?()[]{}'\""
_TEXT_SURROUNDING_PUNCTUATION = " \t\r\n-–—_:;,.!?'\""
_TRANSLATION = str.maketrans(
    {
        "’": "'",
        "‘": "'",
        "ʼ": "'",
        "`": "'",
        "–": " - ",
        "—": " - ",
        "−": " - ",
        "‐": " - ",
        "‑": " - ",
    }
)
_VERSION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("radio_edit", re.compile(r"\bradio\s+edit\b")),
    ("extended", re.compile(r"\bextended(?:\s+mix)?\b")),
    ("sped_up", re.compile(r"\bsped\s+up\b")),
    ("slowed", re.compile(r"\bslowed\b")),
    ("remaster", re.compile(r"\bremaster(?:ed)?\b")),
    ("instrumental", re.compile(r"\binstrumental\b")),
    ("karaoke", re.compile(r"\bkaraoke\b")),
    ("acoustic", re.compile(r"\bacoustic\b")),
    ("remix", re.compile(r"\bremix\b")),
    ("live", re.compile(r"\blive\b")),
    ("demo", re.compile(r"\bdemo\b")),
    ("reverb", re.compile(r"\breverb(?:ed)?\b")),
    ("mono", re.compile(r"\bmono\b")),
    ("stereo", re.compile(r"\bstereo\b")),
    ("clean", re.compile(r"\bclean\b")),
    ("explicit", re.compile(r"\bexplicit\b")),
    ("edit", re.compile(r"\bedit\b")),
    ("mix", re.compile(r"\bmix\b")),
    ("version", re.compile(r"\bversion\b")),
)


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value).translate(_TRANSLATION).casefold()
    normalized = _WHITESPACE.sub(" ", normalized).strip(_TEXT_SURROUNDING_PUNCTUATION)
    return normalized or None


def normalize_isrc(value: str | None) -> str | None:
    if value is None:
        return None
    canonical = re.sub(r"[\s-]+", "", unicodedata.normalize("NFKC", value)).upper()
    return canonical if _ISRC.fullmatch(canonical) else None


def normalize_duration_ms(value: int | None) -> int | None:
    if value is None or isinstance(value, bool) or value <= 0:
        return None
    return value


def normalize_title_artist(
    title: str | None, artist: str | None
) -> tuple[str | None, str | None, frozenset[str]]:
    normalized_title = normalize_text(title)
    normalized_artist = normalize_text(artist)

    title_without_version, markers = _extract_version(normalized_title)
    base_title, featured_artist = _extract_feature(title_without_version)
    if normalized_artist is not None:
        normalized_artist = _FEATURE_TOKEN.sub("feat", normalized_artist)
        normalized_artist = _WHITESPACE.sub(" ", normalized_artist)
    if featured_artist:
        feature_identity = f"feat {featured_artist}"
        if normalized_artist is None:
            normalized_artist = feature_identity
        elif feature_identity not in normalized_artist:
            normalized_artist = f"{normalized_artist} {feature_identity}"

    return base_title, normalized_artist, markers


def extract_version_markers(value: str | None) -> frozenset[str]:
    """Return the repository's canonical version vocabulary for free-form intent."""
    normalized = normalize_text(value)
    return frozenset(_markers_in(normalized or ""))


def identity_from_metadata(metadata: NormalizedTrackMetadata) -> TrackIdentity:
    normalized_title, normalized_artist, markers = normalize_title_artist(
        metadata.title, metadata.artist
    )
    return TrackIdentity(
        provider=metadata.provider,
        provider_track_id=metadata.provider_track_id,
        title=metadata.title,
        artist=metadata.artist,
        album=metadata.album,
        isrc=normalize_isrc(metadata.isrc),
        duration_ms=normalize_duration_ms(metadata.duration_ms),
        explicit=metadata.explicit,
        normalized_title=normalized_title,
        normalized_artist=normalized_artist,
        version_markers=markers,
    )


def identity_from_values(
    *,
    title: str | None,
    artist: str | None,
    album: str | None,
    isrc: str | None,
    duration_ms: int | None,
    explicit: bool | None,
) -> TrackIdentity:
    normalized_title, normalized_artist, markers = normalize_title_artist(title, artist)
    return TrackIdentity(
        provider=None,
        provider_track_id=None,
        title=title,
        artist=artist,
        album=album,
        isrc=normalize_isrc(isrc),
        duration_ms=normalize_duration_ms(duration_ms),
        explicit=explicit,
        normalized_title=normalized_title,
        normalized_artist=normalized_artist,
        version_markers=markers,
    )


def _extract_feature(title: str | None) -> tuple[str | None, str | None]:
    if title is None:
        return None, None
    match = _FEATURE.search(title)
    if match is None:
        return title, None
    featured = match.group(1).strip(_SURROUNDING_PUNCTUATION)
    base = title[: match.start()].strip(_SURROUNDING_PUNCTUATION)
    return base or None, featured or None


def _extract_version(title: str | None) -> tuple[str | None, frozenset[str]]:
    if title is None:
        return None, frozenset()
    markers: set[str] = set()
    base = title
    for match in reversed(tuple(_BRACKETED.finditer(base))):
        found = _markers_in(match.group(1))
        if found:
            markers.update(found)
            base = f"{base[: match.start()]} {base[match.end() :]}"
    suffix = _DASH_SUFFIX.search(base)
    if suffix is not None:
        found = _markers_in(suffix.group(1))
        if found:
            markers.update(found)
            base = base[: suffix.start()]
    base = _WHITESPACE.sub(" ", base).strip(_SURROUNDING_PUNCTUATION)
    return base or None, frozenset(markers)


def _markers_in(value: str) -> set[str]:
    markers = {name for name, pattern in _VERSION_PATTERNS if pattern.search(value)}
    if "radio_edit" in markers:
        markers.discard("edit")
    if "extended" in markers:
        markers.discard("mix")
    if len(markers) > 1:
        markers.discard("version")
    return markers
