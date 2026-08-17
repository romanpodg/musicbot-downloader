from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.core.enums import MusicProviderName, TrackEvidenceCode, TrackMatchDecision
from app.core.models import NormalizedTrackMetadata, TrackIdentity
from app.core.track_identity import identity_from_metadata, normalize_isrc, normalize_text
from app.services.track_matching import match_track_candidates, match_track_identities


def _identity(**changes: Any) -> TrackIdentity:
    metadata = NormalizedTrackMetadata(
        provider=MusicProviderName.SPOTIFY,
        provider_track_id="id",
        source_url="https://example.invalid",
        title="Song",
        artist="Artist",
        album="Album",
        isrc="USABC1234567",
        duration_ms=180_000,
        explicit=False,
    )
    return identity_from_metadata(replace(metadata, **changes))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("usabc1234567", "USABC1234567"),
        (" US-ABC-12-34567 ", "USABC1234567"),
        ("USABC123456", None),
        ("12ABC1234567", None),
        (None, None),
    ],
)
def test_isrc_normalization_is_conservative(raw: str | None, expected: str | None) -> None:
    assert normalize_isrc(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  DON'T   STOP  ", "don't stop"),
        ("Don’t Stop", "don't stop"),
        ("ПРИВЕТ\u00a0— Мир", "привет - мир"),
        ("Ａｒｔｉｓｔ", "artist"),
    ],
)
def test_text_normalization_handles_unicode(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


@pytest.mark.parametrize(
    ("title", "marker"),
    [
        ("Song (Live at Wembley)", "live"),
        ("Song—Live", "live"),
        ("Song - Remix", "remix"),
        ("Song - 2011 Remastered", "remaster"),
        ("Song (Acoustic)", "acoustic"),
        ("Song - Radio Edit", "radio_edit"),
        ("Song (Instrumental)", "instrumental"),
        ("Song - Sped Up", "sped_up"),
        ("Song - Slowed", "slowed"),
    ],
)
def test_version_markers_are_extracted_without_destroying_identity(title: str, marker: str) -> None:
    identity = _identity(title=title)
    assert identity.normalized_title == "song"
    assert marker in identity.version_markers


@pytest.mark.parametrize("feature", ["feat.", "ft.", "featuring"])
def test_featured_artist_representation_is_preserved(feature: str) -> None:
    in_artist = _identity(artist=f"Artist {feature} Guest")
    in_title = _identity(artist="Artist", title=f"Song ({feature} Guest)")
    assert in_artist.normalized_artist == in_title.normalized_artist == "artist feat guest"
    assert in_title.normalized_title == "song"


def test_same_isrc_allows_album_difference() -> None:
    result = match_track_identities(
        _identity(album="Single"), _identity(album="Compilation", duration_ms=182_000)
    )
    assert result.decision is TrackMatchDecision.MATCHED
    assert TrackEvidenceCode.ISRC_MATCH in {item.code for item in result.candidates[0].evidence}


@pytest.mark.parametrize(
    "changes",
    [
        {"title": "Song (Live)"},
        {"title": "Song - Remix"},
        {"title": "Song - 2011 Remaster"},
        {"title": "Song (Acoustic)"},
        {"explicit": True},
        {"duration_ms": 186_000},
    ],
)
def test_hard_contradictions_block_same_isrc(changes: dict[str, object]) -> None:
    result = match_track_identities(_identity(), _identity(**changes))
    assert result.decision is TrackMatchDecision.NEW_TRACK


def test_different_valid_isrc_blocks_title_similarity() -> None:
    result = match_track_identities(_identity(), _identity(isrc="GBABC1234567"))
    assert result.decision is TrackMatchDecision.NEW_TRACK


def test_no_isrc_strict_metadata_fallback_matches() -> None:
    result = match_track_identities(_identity(isrc=None), _identity(isrc=None, duration_ms=182_999))
    assert result.decision is TrackMatchDecision.MATCHED


def test_no_isrc_without_duration_is_ambiguous() -> None:
    result = match_track_identities(
        _identity(isrc=None, duration_ms=None), _identity(isrc=None, duration_ms=None)
    )
    assert result.decision is TrackMatchDecision.AMBIGUOUS


def test_duplicate_isrc_selects_only_uniquely_compatible_candidate() -> None:
    result = match_track_candidates(
        _identity(),
        (
            (1, _identity(title="Song (Live)")),
            (2, _identity(duration_ms=181_000)),
        ),
    )
    assert result.decision is TrackMatchDecision.MATCHED
    assert result.matched_track_id == 2


def test_equivalent_specific_version_markers_do_not_conflict() -> None:
    result = match_track_identities(
        _identity(title="Song (Extended Mix)"), _identity(title="Song (Extended)")
    )
    assert result.decision is TrackMatchDecision.MATCHED
