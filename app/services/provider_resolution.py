"""Resolve verified TrackSources into currently usable provider candidates."""

from __future__ import annotations

import logging

from app.core.enums import ProviderResolutionStatus, ProviderRuntimeStatus
from app.core.exceptions import (
    InvalidTrackUrl,
    MetadataUnavailable,
    ProviderAuthenticationError,
    ProviderUnavailable,
    TrackNotFound,
    UnsupportedProvider,
)
from app.core.models import (
    DownloadProviderCandidate,
    ProviderCandidateFailure,
    ProviderCapabilities,
    ProviderMediaCapabilities,
    ProviderResolutionResult,
    ProviderSourceCheck,
)
from app.core.provider_resolution import ProviderCandidate, ProviderCandidateRanker
from app.providers.base import MusicProvider
from app.services.provider_candidates import ProviderCandidateResolver
from app.storage import Database
from app.storage.models import TrackSource

logger = logging.getLogger(__name__)

__all__ = [
    "ProviderResolver",
    "ProviderCandidateResolver",
    "ProviderCandidate",
    "ProviderCandidateRanker",
]


class ProviderResolver:
    """Report provider facts for persisted, verified sources without ranking them."""

    def __init__(self, database: Database, provider: MusicProvider) -> None:
        self._database = database
        self._provider = provider

    async def resolve(self, track_id: int) -> ProviderResolutionResult:
        async with self._database.transaction() as repositories:
            track = await repositories.tracks.get_track_by_id(track_id)
            if track is None:
                raise TrackNotFound()
            sources = await repositories.track_sources.get_sources_for_track(track_id)

        ordered_sources = sorted(
            sources,
            key=lambda source: (source.provider.value, source.id),
        )
        candidates: list[DownloadProviderCandidate] = []
        failures: list[ProviderCandidateFailure] = []
        for source in ordered_sources:
            try:
                capabilities = self._provider.provider_capabilities(source.provider)
            except Exception:
                capabilities = _unknown_capabilities()
                check = ProviderSourceCheck(
                    ProviderRuntimeStatus.ERROR,
                    error_code="capability_query_failed",
                )
            else:
                check = await self._check_one(source, capabilities.download_supported)
            logger.info(
                "Provider source resolved",
                extra={
                    "track_id": track_id,
                    "track_source_id": source.id,
                    "provider": source.provider.value,
                    "runtime_status": check.status.value,
                },
            )
            if check.status is ProviderRuntimeStatus.AVAILABLE:
                candidates.append(
                    DownloadProviderCandidate(
                        track_id=track_id,
                        track_source_id=source.id,
                        provider=source.provider,
                        provider_track_id=source.provider_track_id,
                        runtime_status=check.status,
                        capabilities=capabilities,
                        native_media_info=check.native_media_info,
                    )
                )
            else:
                failures.append(
                    ProviderCandidateFailure(
                        track_id=track_id,
                        track_source_id=source.id,
                        provider=source.provider,
                        provider_track_id=source.provider_track_id,
                        runtime_status=check.status,
                        capabilities=capabilities,
                        error_code=check.error_code,
                    )
                )

        status = (
            ProviderResolutionStatus.AVAILABLE
            if candidates
            else ProviderResolutionStatus.NO_AVAILABLE_PROVIDER
        )
        return ProviderResolutionResult(track_id, status, tuple(candidates), tuple(failures))

    async def _check_one(
        self, source: TrackSource, download_supported: bool
    ) -> ProviderSourceCheck:
        if not download_supported:
            return ProviderSourceCheck(
                ProviderRuntimeStatus.UNSUPPORTED,
                error_code="provider_not_downloadable",
            )
        try:
            return await self._provider.check_source(
                source.provider,
                source.provider_track_id,
            )
        except ProviderAuthenticationError:
            return ProviderSourceCheck(
                ProviderRuntimeStatus.AUTH_REQUIRED,
                error_code="authentication_required",
            )
        except UnsupportedProvider:
            return ProviderSourceCheck(
                ProviderRuntimeStatus.UNSUPPORTED,
                error_code="provider_not_downloadable",
            )
        except ProviderUnavailable:
            return ProviderSourceCheck(
                ProviderRuntimeStatus.UNAVAILABLE,
                error_code="provider_unavailable",
            )
        except (MetadataUnavailable, InvalidTrackUrl):
            return ProviderSourceCheck(
                ProviderRuntimeStatus.ERROR,
                error_code="source_check_failed",
            )
        except Exception:
            return ProviderSourceCheck(
                ProviderRuntimeStatus.ERROR,
                error_code="unexpected_provider_error",
            )


def _unknown_capabilities() -> ProviderCapabilities:
    return ProviderCapabilities(
        metadata_supported=False,
        search_supported=False,
        download_supported=False,
        requires_auth=None,
        media=ProviderMediaCapabilities(known=False),
    )
