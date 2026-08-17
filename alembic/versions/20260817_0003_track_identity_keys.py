"""Add normalized identity lookup keys.

Revision ID: 20260817_0003
Revises: 20260817_0002
Create Date: 2026-08-17
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260817_0003"
down_revision: str | None = "20260817_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WHITESPACE = re.compile(r"\s+")
_FEATURE = re.compile(r"\b(?:feat(?:uring)?|ft)\.?\s+(.+)$")
_FEATURE_TOKEN = re.compile(r"\b(?:feat(?:uring)?|ft)\.?(?=\s|$)")
_BRACKETED = re.compile(r"[\(\[\{]([^\)\]\}]+)[\)\]\}]")
_DASH_SUFFIX = re.compile(r"\s+-\s+(.+)$")
_PUNCTUATION = " \t\r\n-–—_:;,.!?()[]{}'\""
_TEXT_PUNCTUATION = " \t\r\n-–—_:;,.!?'\""
_ISRC = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}\d{7}$")
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
_MARKERS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bradio\s+edit\b",
        r"\bextended(?:\s+mix)?\b",
        r"\bsped\s+up\b",
        r"\bslowed\b",
        r"\bremaster(?:ed)?\b",
        r"\binstrumental\b",
        r"\bkaraoke\b",
        r"\bacoustic\b",
        r"\bremix\b",
        r"\blive\b",
        r"\bdemo\b",
        r"\breverb(?:ed)?\b",
        r"\bmono\b",
        r"\bstereo\b",
        r"\bclean\b",
        r"\bexplicit\b",
        r"\bedit\b",
        r"\bmix\b",
        r"\bversion\b",
    )
)


def upgrade() -> None:
    op.add_column("tracks", sa.Column("normalized_title", sa.String(length=512), nullable=True))
    op.add_column("tracks", sa.Column("normalized_artist", sa.String(length=512), nullable=True))
    tracks = sa.table(
        "tracks",
        sa.column("id", sa.Integer),
        sa.column("title", sa.String),
        sa.column("artist", sa.String),
        sa.column("isrc", sa.String),
        sa.column("normalized_title", sa.String),
        sa.column("normalized_artist", sa.String),
    )
    connection = op.get_bind()
    for row in connection.execute(
        sa.select(tracks.c.id, tracks.c.title, tracks.c.artist, tracks.c.isrc)
    ):
        normalized_title, normalized_artist = _identity_keys(row.title, row.artist)
        connection.execute(
            tracks.update()
            .where(tracks.c.id == row.id)
            .values(
                normalized_title=normalized_title,
                normalized_artist=normalized_artist,
                isrc=_normalize_isrc(row.isrc),
            )
        )
    op.create_index(
        "ix_tracks_normalized_artist_title",
        "tracks",
        ["normalized_artist", "normalized_title"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tracks_normalized_artist_title", table_name="tracks")
    op.drop_column("tracks", "normalized_artist")
    op.drop_column("tracks", "normalized_title")


def _identity_keys(title: str | None, artist: str | None) -> tuple[str | None, str | None]:
    normalized_title = _normalize(title)
    normalized_artist = _normalize(artist)
    featured = None
    if normalized_title:
        for match in reversed(tuple(_BRACKETED.finditer(normalized_title))):
            if any(pattern.search(match.group(1)) for pattern in _MARKERS):
                normalized_title = (
                    f"{normalized_title[: match.start()]} {normalized_title[match.end() :]}"
                )
        suffix = _DASH_SUFFIX.search(normalized_title)
        if suffix and any(pattern.search(suffix.group(1)) for pattern in _MARKERS):
            normalized_title = normalized_title[: suffix.start()]
        normalized_title = _WHITESPACE.sub(" ", normalized_title).strip(_PUNCTUATION) or None
    if normalized_title:
        feature_match = _FEATURE.search(normalized_title)
        if feature_match:
            featured = feature_match.group(1).strip(_PUNCTUATION) or None
            normalized_title = normalized_title[: feature_match.start()].strip(_PUNCTUATION) or None
    if normalized_artist:
        normalized_artist = _WHITESPACE.sub(" ", _FEATURE_TOKEN.sub("feat", normalized_artist))
    if featured:
        feature_identity = f"feat {featured}"
        normalized_artist = (
            feature_identity
            if normalized_artist is None
            else f"{normalized_artist} {feature_identity}"
            if feature_identity not in normalized_artist
            else normalized_artist
        )
    return normalized_title, normalized_artist


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    value = unicodedata.normalize("NFKC", value).translate(_TRANSLATION).casefold()
    return _WHITESPACE.sub(" ", value).strip(_TEXT_PUNCTUATION) or None


def _normalize_isrc(value: str | None) -> str | None:
    if value is None:
        return None
    canonical = re.sub(r"[\s-]+", "", unicodedata.normalize("NFKC", value)).upper()
    return canonical if _ISRC.fullmatch(canonical) else None
