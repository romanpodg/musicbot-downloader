"""Bounded provider candidate discovery for Stage 25."""

from __future__ import annotations

import logging

from app.core.enums import MusicProviderName, QualityPreference
from app.core.exceptions import (
    ProviderAuthenticationError,
    ProviderUnavailable,
    UnsupportedProvider,
)
from app.core.models import ProviderCapabilities, ProviderMediaCapabilities
from app.core.provider_resolution import (
    CanonicalMediaIdentity,
    ProviderCandidate,
    match_media,
)
from app.core.track_identity import identity_from_metadata
from app.providers.base import MusicProvider
from app.storage.models.provider_resolution import DownloadProviderCandidateRecord

logger = logging.getLogger(__name__)
MAX_CANDIDATES_PER_PROVIDER = 10


class ProviderCandidateResolver:
    """Discover and normalize candidates; never downloads or selects accounts."""

    def __init__(
        self,
        provider: MusicProvider,
        database: object | None = None,
        *,
        max_candidates_per_provider: int = MAX_CANDIDATES_PER_PROVIDER,
    ) -> None:
        self._provider = provider
        self._database = database
        self._limit = max(1, min(max_candidates_per_provider, 50))

    async def resolve(
        self,
        identity: CanonicalMediaIdentity,
        *,
        source_provider: MusicProviderName | None = None,
        source_media_id: str | None = None,
        request_id: int | None = None,
    ) -> tuple[ProviderCandidate, ...]:
        try:
            providers = await self._provider.list_searchable_providers()
        except (ProviderUnavailable, ProviderAuthenticationError, UnsupportedProvider):
            providers = ()
        ordered = list(dict.fromkeys(providers))
        if self._database is not None and request_id is not None:
            async with self._database.transaction() as repositories:  # type: ignore[attr-defined]
                persisted = await repositories.provider_resolution.list_candidates(request_id)
            if persisted:
                return tuple(self._from_record(row) for row in persisted)
        if source_provider is not None and source_provider not in ordered:
            ordered.insert(0, source_provider)
        results: list[ProviderCandidate] = []
        for provider in ordered:
            if source_provider is provider and source_media_id:
                try:
                    metadata = await self._provider.get_track_metadata(provider, source_media_id)
                    candidate = self._candidate(identity, metadata, provider, source_media_id)
                    if candidate.match.method.name != "REJECTED":
                        results.append(candidate)
                except Exception:
                    logger.info(
                        "provider source candidate unavailable", extra={"provider": provider.value}
                    )
            try:
                from app.core.models import TrackSearchRequest

                query = f"{identity.artist} {identity.title}"
                searched = await self._provider.search_tracks(
                    TrackSearchRequest(provider, query, self._limit)
                )
            except Exception:
                continue
            seen: set[tuple[MusicProviderName, str]] = set()
            for item in searched[: self._limit]:
                key = (item.provider, item.provider_track_id)
                if key in seen or item.provider is not provider:
                    continue
                seen.add(key)
                try:
                    metadata = await self._provider.get_track_metadata(
                        provider, item.provider_track_id
                    )
                    candidate = self._candidate(
                        identity, metadata, provider, item.provider_track_id, item.url
                    )
                except Exception:
                    continue
                if candidate.match.method.name != "REJECTED":
                    results.append(candidate)
        unique: dict[tuple[MusicProviderName, str], ProviderCandidate] = {}
        for candidate in results:
            unique.setdefault((candidate.provider, candidate.provider_media_id), candidate)
        snapshot = tuple(unique.values())
        if self._database is not None and request_id is not None:
            async with self._database.transaction() as repositories:  # type: ignore[attr-defined]
                from app.storage.models.base import utc_now

                for candidate in snapshot:
                    await repositories.provider_resolution.upsert_candidate(
                        request_id, candidate, utc_now()
                    )
        return snapshot

    def _candidate(
        self,
        identity: CanonicalMediaIdentity,
        metadata: object,
        provider: MusicProviderName,
        media_id: str,
        source_reference: str | None = None,
    ) -> ProviderCandidate:
        normalized = identity_from_metadata(metadata)  # type: ignore[arg-type]
        candidate_identity = CanonicalMediaIdentity.from_values(
            title=normalized.title or "",
            artist=normalized.artist or "",
            album=normalized.album,
            isrc=normalized.isrc,
            duration_ms=normalized.duration_ms,
        )
        return ProviderCandidate(
            provider,
            media_id,
            candidate_identity,
            match_media(identity, candidate_identity),
            self._capabilities(provider).media,
            source_reference,
        )

    def _capabilities(self, provider: MusicProviderName) -> ProviderCapabilities:
        return self._provider.provider_capabilities(provider)

    @staticmethod
    def _from_record(row: DownloadProviderCandidateRecord) -> ProviderCandidate:
        snapshot = row.identity_snapshot
        identity = CanonicalMediaIdentity.from_values(
            title=snapshot["title"],
            artist=snapshot["artist"],
            album=snapshot.get("album"),
            isrc=snapshot.get("isrc"),
            duration_ms=snapshot.get("duration_ms"),
        )
        capabilities = row.media_capabilities
        media = ProviderMediaCapabilities(
            known=bool(capabilities.get("known", True)),
            supports_lossy=capabilities.get("supports_lossy"),
            supports_lossless=capabilities.get("supports_lossless"),
            qualities=frozenset(
                QualityPreference(item) for item in capabilities.get("qualities", ())
            ),
        )
        from app.core.provider_resolution import MatchMethod, MediaMatch

        match = MediaMatch(
            float(row.match_score),
            MatchMethod(row.match_method),
            tuple(row.match_reasons),
            MatchMethod(row.match_method) in {MatchMethod.ISRC_EXACT, MatchMethod.METADATA_STRONG},
        )
        return ProviderCandidate(
            MusicProviderName(row.provider),
            row.provider_media_id,
            identity,
            match,
            media,
            row.source_reference,
        )


__all__ = ["ProviderCandidateResolver", "resolve_after_cache_miss"]


async def resolve_after_cache_miss(
    cache_lookup: object,
    resolver: ProviderCandidateResolver,
    identity: CanonicalMediaIdentity,
    *,
    source_provider: MusicProviderName | None = None,
    source_media_id: str | None = None,
    request_id: int | None = None,
) -> tuple[bool, tuple[ProviderCandidate, ...]]:
    """Run Stage 25 only after a Stage 24 cache lookup reports a miss.

    The callback is intentionally injected so delivery/cache ownership stays in
    Stage 24 and this helper cannot create a second lifecycle.
    """
    hit = await cache_lookup()  # type: ignore[operator]
    if hit is not None:
        return True, ()
    return False, await resolver.resolve(
        identity,
        source_provider=source_provider,
        source_media_id=source_media_id,
        request_id=request_id,
    )
