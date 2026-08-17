"""Explainable deterministic rules for deciding recording identity."""

from __future__ import annotations

from collections.abc import Iterable

from app.core.enums import TrackEvidenceCode, TrackMatchDecision
from app.core.models import (
    TrackIdentity,
    TrackMatchCandidate,
    TrackMatchEvidence,
    TrackMatchResult,
)

STRICT_DURATION_TOLERANCE_MS = 3_000
LOOSE_DURATION_TOLERANCE_MS = 5_000


def match_track_candidates(
    incoming: TrackIdentity, candidates: Iterable[tuple[int, TrackIdentity]]
) -> TrackMatchResult:
    evaluated = tuple(_evaluate(incoming, track_id, identity) for track_id, identity in candidates)
    compatible = [candidate for candidate in evaluated if candidate.compatible]
    if len(compatible) == 1:
        return TrackMatchResult(TrackMatchDecision.MATCHED, compatible[0].track_id, evaluated)
    if len(compatible) > 1 or any(candidate.incomplete for candidate in evaluated):
        return TrackMatchResult(TrackMatchDecision.AMBIGUOUS, None, evaluated)
    return TrackMatchResult(TrackMatchDecision.NEW_TRACK, None, evaluated)


def match_track_identities(incoming: TrackIdentity, candidate: TrackIdentity) -> TrackMatchResult:
    return match_track_candidates(incoming, ((0, candidate),))


def _evaluate(
    incoming: TrackIdentity, track_id: int, candidate: TrackIdentity
) -> TrackMatchCandidate:
    evidence: list[TrackMatchEvidence] = []
    title_match = _compare_text(
        incoming.normalized_title,
        candidate.normalized_title,
        TrackEvidenceCode.TITLE_MATCH,
        TrackEvidenceCode.TITLE_CONFLICT,
        evidence,
    )
    artist_match = _compare_text(
        incoming.normalized_artist,
        candidate.normalized_artist,
        TrackEvidenceCode.ARTIST_MATCH,
        TrackEvidenceCode.ARTIST_CONFLICT,
        evidence,
    )

    version_conflict = incoming.version_markers != candidate.version_markers
    evidence.append(
        TrackMatchEvidence(
            TrackEvidenceCode.VERSION_CONFLICT
            if version_conflict
            else TrackEvidenceCode.VERSION_MATCH,
            _version_detail(incoming, candidate),
        )
    )

    explicit_conflict = (
        incoming.explicit is not None
        and candidate.explicit is not None
        and incoming.explicit != candidate.explicit
    )
    if explicit_conflict:
        evidence.append(TrackMatchEvidence(TrackEvidenceCode.EXPLICIT_CONFLICT))
    elif incoming.explicit is not None and candidate.explicit is not None:
        evidence.append(TrackMatchEvidence(TrackEvidenceCode.EXPLICIT_MATCH))

    duration_code = _duration_evidence(incoming.duration_ms, candidate.duration_ms)
    evidence.append(
        TrackMatchEvidence(
            duration_code,
            _duration_detail(incoming.duration_ms, candidate.duration_ms),
        )
    )

    if (
        incoming.album is not None
        and candidate.album is not None
        and incoming.album == candidate.album
    ):
        evidence.append(TrackMatchEvidence(TrackEvidenceCode.ALBUM_MATCH))

    same_isrc = incoming.isrc is not None and incoming.isrc == candidate.isrc
    conflicting_isrc = (
        incoming.isrc is not None and candidate.isrc is not None and incoming.isrc != candidate.isrc
    )
    if same_isrc:
        evidence.append(TrackMatchEvidence(TrackEvidenceCode.ISRC_MATCH))
    elif conflicting_isrc:
        evidence.append(TrackMatchEvidence(TrackEvidenceCode.ISRC_CONFLICT))

    hard_conflict = (
        version_conflict
        or explicit_conflict
        or title_match is False
        or artist_match is False
        or duration_code is TrackEvidenceCode.DURATION_CONFLICT
    )
    if same_isrc:
        compatible = not hard_conflict
        incomplete = False
    elif conflicting_isrc:
        compatible = False
        incomplete = False
    else:
        compatible = (
            title_match is True
            and artist_match is True
            and duration_code is TrackEvidenceCode.DURATION_MATCH
            and not version_conflict
            and not explicit_conflict
        )
        incomplete = (
            not compatible
            and title_match is True
            and artist_match is True
            and not version_conflict
            and not explicit_conflict
            and duration_code
            in {TrackEvidenceCode.DURATION_UNKNOWN, TrackEvidenceCode.DURATION_LOOSE}
        )
    return TrackMatchCandidate(track_id, candidate, tuple(evidence), compatible, incomplete)


def _compare_text(
    left: str | None,
    right: str | None,
    match_code: TrackEvidenceCode,
    conflict_code: TrackEvidenceCode,
    evidence: list[TrackMatchEvidence],
) -> bool | None:
    if left is None or right is None:
        return None
    matches = left == right
    evidence.append(TrackMatchEvidence(match_code if matches else conflict_code))
    return matches


def _duration_evidence(left: int | None, right: int | None) -> TrackEvidenceCode:
    if left is None or right is None:
        return TrackEvidenceCode.DURATION_UNKNOWN
    delta = abs(left - right)
    if delta <= STRICT_DURATION_TOLERANCE_MS:
        return TrackEvidenceCode.DURATION_MATCH
    if delta <= LOOSE_DURATION_TOLERANCE_MS:
        return TrackEvidenceCode.DURATION_LOOSE
    return TrackEvidenceCode.DURATION_CONFLICT


def _duration_detail(left: int | None, right: int | None) -> str | None:
    if left is None or right is None:
        return None
    return f"delta_ms={abs(left - right)}"


def _version_detail(left: TrackIdentity, right: TrackIdentity) -> str:
    incoming = ",".join(sorted(left.version_markers)) or "studio"
    candidate = ",".join(sorted(right.version_markers)) or "studio"
    return f"incoming={incoming};candidate={candidate}"
