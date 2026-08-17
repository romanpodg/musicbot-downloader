"""Resolve one recording to a canonical Track and verified provider sources."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.core.enums import (
    MusicProviderName,
    ProviderDiscoveryStatus,
    TrackEvidenceCode,
    TrackMatchDecision,
)
from app.core.exceptions import (
    DatabaseConcurrencyError,
    DatabaseError,
    InvalidTrackUrl,
    MetadataUnavailable,
    ProviderAuthenticationError,
    ProviderUnavailable,
    TrackSourceOwnershipConflict,
    UnsupportedProvider,
)
from app.core.models import (
    NormalizedTrackMetadata,
    ProviderDiscoveryResult,
    TrackIdentity,
    TrackMatchEvidence,
    TrackMatchResult,
    TrackSearchRequest,
)
from app.core.track_identity import identity_from_metadata, identity_from_values
from app.providers.base import MusicProvider
from app.services.track_matching import match_track_candidates, match_track_identities
from app.storage.database import Database
from app.storage.models import Track, TrackSource
from app.storage.repositories.tracks import MAX_DATABASE_CANDIDATES, TrackRepository

_MAX_PERSIST_ATTEMPTS = 4
MAX_SEARCH_RESULTS_PER_PROVIDER = 10


@dataclass(frozen=True, slots=True)
class ResolveResult:
    metadata: NormalizedTrackMetadata
    identity: TrackIdentity
    track: Track
    source: TrackSource
    input_decision: TrackMatchDecision
    input_evidence: tuple[TrackMatchEvidence, ...]
    input_match: TrackMatchResult | None
    track_created: bool
    source_created: bool
    discoveries: tuple[ProviderDiscoveryResult, ...] = ()


class ResolveTrackService:
    def __init__(self, database: Database, provider: MusicProvider) -> None:
        self._database = database
        self._provider = provider

    async def resolve(self, url: str, *, discover: bool = False) -> ResolveResult:
        metadata = await self._provider.get_metadata(url)
        identity = identity_from_metadata(metadata)
        result = await self._retry_persist_input(metadata, identity)
        if not discover:
            return result
        discoveries = await self._discover(result.track, identity)
        return ResolveResult(
            metadata=result.metadata,
            identity=result.identity,
            track=result.track,
            source=result.source,
            input_decision=result.input_decision,
            input_evidence=result.input_evidence,
            input_match=result.input_match,
            track_created=result.track_created,
            source_created=result.source_created,
            discoveries=discoveries,
        )

    async def _retry_persist_input(
        self, metadata: NormalizedTrackMetadata, identity: TrackIdentity
    ) -> ResolveResult:
        for attempt in range(_MAX_PERSIST_ATTEMPTS):
            try:
                return await self._persist_input(metadata, identity)
            except (DatabaseConcurrencyError, TrackSourceOwnershipConflict):
                if attempt == _MAX_PERSIST_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(0.02 * (attempt + 1))
        raise DatabaseError()

    async def _persist_input(
        self, metadata: NormalizedTrackMetadata, identity: TrackIdentity
    ) -> ResolveResult:
        async with self._database.transaction() as repositories:
            existing_source = await repositories.track_sources.get_source(
                metadata.provider, metadata.provider_track_id
            )
            if existing_source is not None:
                track = await repositories.tracks.get_track_by_id(existing_source.track_id)
                if track is None:
                    raise DatabaseError()
                await self._enrich(repositories.tracks, track, metadata)
                source_result = await repositories.track_sources.upsert_source(
                    track_id=track.id,
                    provider=metadata.provider,
                    provider_track_id=metadata.provider_track_id,
                    url=metadata.source_url,
                    provider_metadata=metadata.provider_metadata,
                )
                exact = (TrackMatchEvidence(TrackEvidenceCode.EXACT_SOURCE),)
                return ResolveResult(
                    metadata,
                    identity,
                    track,
                    source_result.source,
                    TrackMatchDecision.MATCHED,
                    exact,
                    None,
                    False,
                    source_result.created,
                )

            candidates: dict[int, Track] = {}
            candidates_truncated = False
            if identity.isrc is not None:
                isrc_candidates = await repositories.tracks.get_tracks_by_isrc(identity.isrc)
                candidates_truncated = len(isrc_candidates) > MAX_DATABASE_CANDIDATES
                for track in isrc_candidates[:MAX_DATABASE_CANDIDATES]:
                    candidates[track.id] = track
            if identity.normalized_title and identity.normalized_artist:
                metadata_candidates = await repositories.tracks.get_tracks_by_normalized_identity(
                    identity.normalized_title, identity.normalized_artist
                )
                candidates_truncated = (
                    candidates_truncated or len(metadata_candidates) > MAX_DATABASE_CANDIDATES
                )
                for track in metadata_candidates[:MAX_DATABASE_CANDIDATES]:
                    candidates[track.id] = track

            ordered_candidates = sorted(candidates.values(), key=lambda value: value.id)
            if len(ordered_candidates) > MAX_DATABASE_CANDIDATES:
                candidates_truncated = True
                ordered_candidates = ordered_candidates[:MAX_DATABASE_CANDIDATES]

            match = match_track_candidates(
                identity,
                ((track.id, self._track_identity(track)) for track in ordered_candidates),
            )
            if candidates_truncated:
                match = TrackMatchResult(TrackMatchDecision.AMBIGUOUS, None, match.candidates)
            matched_candidate = (
                candidates.get(match.matched_track_id)
                if match.decision is TrackMatchDecision.MATCHED
                and match.matched_track_id is not None
                else None
            )
            track_created = matched_candidate is None
            if matched_candidate is None:
                track = await repositories.tracks.create_track(
                    isrc=identity.isrc,
                    title=metadata.title,
                    artist=metadata.artist,
                    album=metadata.album,
                    duration_ms=identity.duration_ms,
                    release_date=metadata.release_date,
                    explicit=metadata.explicit,
                )
            else:
                track = matched_candidate
                await self._enrich(repositories.tracks, track, metadata)

            source_result = await repositories.track_sources.upsert_source(
                track_id=track.id,
                provider=metadata.provider,
                provider_track_id=metadata.provider_track_id,
                url=metadata.source_url,
                provider_metadata=metadata.provider_metadata,
            )
            return ResolveResult(
                metadata,
                identity,
                track,
                source_result.source,
                match.decision,
                self._matched_evidence(match),
                match,
                track_created,
                source_result.created,
            )

    async def _discover(
        self, canonical_track: Track, incoming: TrackIdentity
    ) -> tuple[ProviderDiscoveryResult, ...]:
        try:
            searchable = await self._provider.list_searchable_providers()
        except ProviderAuthenticationError:
            return self._all_provider_failures(
                incoming,
                ProviderDiscoveryStatus.AUTH_REQUIRED,
                "provider_authentication_error",
            )
        except (ProviderUnavailable, MetadataUnavailable):
            return self._all_provider_failures(
                incoming, ProviderDiscoveryStatus.UNAVAILABLE, "provider_unavailable"
            )
        except Exception:
            return self._all_provider_failures(
                incoming, ProviderDiscoveryStatus.ERROR, "unexpected_provider_error"
            )

        results: list[ProviderDiscoveryResult] = []
        for target in dict.fromkeys(searchable):
            if target is incoming.provider:
                continue
            try:
                result = await self._discover_provider(canonical_track, incoming, target)
            except Exception:
                result = ProviderDiscoveryResult(
                    target,
                    ProviderDiscoveryStatus.ERROR,
                    error_code="unexpected_provider_error",
                )
            results.append(result)
        return tuple(results)

    async def _discover_provider(
        self,
        canonical_track: Track,
        incoming: TrackIdentity,
        target: MusicProviderName,
    ) -> ProviderDiscoveryResult:
        if incoming.title is None or incoming.artist is None:
            return ProviderDiscoveryResult(target, ProviderDiscoveryStatus.AMBIGUOUS)
        request = TrackSearchRequest(
            target_provider=target,
            query=f"{incoming.artist} {incoming.title}",
            limit=MAX_SEARCH_RESULTS_PER_PROVIDER,
        )
        try:
            search_results = await self._provider.search_tracks(request)
        except ProviderAuthenticationError:
            return ProviderDiscoveryResult(
                target,
                ProviderDiscoveryStatus.AUTH_REQUIRED,
                error_code="provider_authentication_error",
            )
        except (ProviderUnavailable, UnsupportedProvider):
            return ProviderDiscoveryResult(
                target, ProviderDiscoveryStatus.UNAVAILABLE, error_code="provider_unavailable"
            )
        except (MetadataUnavailable, InvalidTrackUrl):
            return ProviderDiscoveryResult(
                target, ProviderDiscoveryStatus.ERROR, error_code="metadata_unavailable"
            )

        unique_results = {
            (candidate.provider, candidate.provider_track_id): candidate
            for candidate in search_results[:MAX_SEARCH_RESULTS_PER_PROVIDER]
            if candidate.provider is target
        }
        verified: list[tuple[NormalizedTrackMetadata, tuple[TrackMatchEvidence, ...]]] = []
        ambiguous = False
        metadata_failures = 0
        for candidate in unique_results.values():
            try:
                metadata = await self._provider.get_metadata(candidate.url)
            except ProviderAuthenticationError:
                return ProviderDiscoveryResult(
                    target,
                    ProviderDiscoveryStatus.AUTH_REQUIRED,
                    error_code="provider_authentication_error",
                )
            except (ProviderUnavailable, UnsupportedProvider):
                return ProviderDiscoveryResult(
                    target,
                    ProviderDiscoveryStatus.UNAVAILABLE,
                    error_code="provider_unavailable",
                )
            except (MetadataUnavailable, InvalidTrackUrl):
                metadata_failures += 1
                continue
            if metadata.provider is not target:
                metadata_failures += 1
                continue
            match = match_track_identities(incoming, identity_from_metadata(metadata))
            if match.decision is TrackMatchDecision.MATCHED:
                verified.append((metadata, self._matched_evidence(match)))
            elif match.decision is TrackMatchDecision.AMBIGUOUS:
                ambiguous = True

        if len(verified) > 1:
            return ProviderDiscoveryResult(target, ProviderDiscoveryStatus.AMBIGUOUS)
        if not verified:
            if ambiguous:
                return ProviderDiscoveryResult(target, ProviderDiscoveryStatus.AMBIGUOUS)
            if metadata_failures and metadata_failures == len(unique_results):
                return ProviderDiscoveryResult(
                    target, ProviderDiscoveryStatus.ERROR, error_code="metadata_unavailable"
                )
            return ProviderDiscoveryResult(target, ProviderDiscoveryStatus.NO_MATCH)

        metadata, evidence = verified[0]
        return await self._attach_discovered(canonical_track.id, metadata, evidence)

    async def _attach_discovered(
        self,
        canonical_track_id: int,
        metadata: NormalizedTrackMetadata,
        evidence: tuple[TrackMatchEvidence, ...],
    ) -> ProviderDiscoveryResult:
        for attempt in range(_MAX_PERSIST_ATTEMPTS):
            try:
                async with self._database.transaction() as repositories:
                    existing = await repositories.track_sources.get_source(
                        metadata.provider, metadata.provider_track_id
                    )
                    if existing is not None and existing.track_id != canonical_track_id:
                        return ProviderDiscoveryResult(
                            metadata.provider,
                            ProviderDiscoveryStatus.IDENTITY_CONFLICT,
                            metadata.provider_track_id,
                            evidence,
                        )
                    track = await repositories.tracks.get_track_by_id(canonical_track_id)
                    if track is None:
                        raise DatabaseError()
                    await self._enrich(repositories.tracks, track, metadata)
                    await repositories.track_sources.upsert_source(
                        track_id=track.id,
                        provider=metadata.provider,
                        provider_track_id=metadata.provider_track_id,
                        url=metadata.source_url,
                        provider_metadata=metadata.provider_metadata,
                    )
                    return ProviderDiscoveryResult(
                        metadata.provider,
                        ProviderDiscoveryStatus.MATCHED,
                        metadata.provider_track_id,
                        evidence,
                    )
            except (DatabaseConcurrencyError, TrackSourceOwnershipConflict):
                if attempt == _MAX_PERSIST_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(0.02 * (attempt + 1))
        raise DatabaseError()

    @staticmethod
    async def _enrich(
        repository: TrackRepository, track: Track, metadata: NormalizedTrackMetadata
    ) -> None:
        await repository.enrich_missing(
            track,
            isrc=metadata.isrc,
            title=metadata.title,
            artist=metadata.artist,
            album=metadata.album,
            duration_ms=metadata.duration_ms,
            release_date=metadata.release_date,
            explicit=metadata.explicit,
        )

    @staticmethod
    def _track_identity(track: Track) -> TrackIdentity:
        return identity_from_values(
            title=track.title,
            artist=track.artist,
            album=track.album,
            isrc=track.isrc,
            duration_ms=track.duration_ms,
            explicit=track.explicit,
        )

    @staticmethod
    def _matched_evidence(match: TrackMatchResult) -> tuple[TrackMatchEvidence, ...]:
        if match.matched_track_id is None:
            return ()
        for candidate in match.candidates:
            if candidate.track_id == match.matched_track_id:
                return candidate.evidence
        return ()

    @staticmethod
    def _all_provider_failures(
        incoming: TrackIdentity, status: ProviderDiscoveryStatus, error_code: str
    ) -> tuple[ProviderDiscoveryResult, ...]:
        return tuple(
            ProviderDiscoveryResult(provider, status, error_code=error_code)
            for provider in MusicProviderName
            if provider is not incoming.provider
        )
