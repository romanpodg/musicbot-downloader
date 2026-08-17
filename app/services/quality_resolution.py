"""Resolve fresh provider state into ordered quality-safe download plans."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol

from app.core.enums import (
    ProviderRuntimeStatus,
    QualityCandidateRejectionReason,
    QualityProfile,
    QualityResolutionStatus,
)
from app.core.models import (
    DownloadPlan,
    ProviderResolutionResult,
    QualityProviderDiagnostic,
    QualityResolutionResult,
)
from app.core.quality import plan_sort_key, plans_for_candidate, rejection_reason_for_candidate

logger = logging.getLogger(__name__)


class ProviderResolverBoundary(Protocol):
    async def resolve(self, track_id: int) -> ProviderResolutionResult: ...


class QualityResolver:
    """Apply quality policy to a new Stage 4 snapshot on every call."""

    def __init__(self, provider_resolver: ProviderResolverBoundary) -> None:
        self._provider_resolver = provider_resolver

    async def resolve(
        self,
        track_id: int,
        quality_profile: QualityProfile,
    ) -> QualityResolutionResult:
        provider_result = await self._provider_resolver.resolve(track_id)
        plans: list[DownloadPlan] = []
        diagnostics: list[QualityProviderDiagnostic] = []

        for candidate in provider_result.candidates:
            candidate_plans = (
                plans_for_candidate(candidate, quality_profile)
                if candidate.runtime_status is ProviderRuntimeStatus.AVAILABLE
                else ()
            )
            plans.extend(candidate_plans)
            rejection_reason = None
            if not candidate_plans:
                rejection_reason = (
                    rejection_reason_for_candidate(candidate, quality_profile)
                    if candidate.runtime_status is ProviderRuntimeStatus.AVAILABLE
                    else QualityCandidateRejectionReason.PROVIDER_NOT_AVAILABLE
                )
            diagnostics.append(
                QualityProviderDiagnostic(
                    track_id=candidate.track_id,
                    track_source_id=candidate.track_source_id,
                    provider=candidate.provider,
                    provider_track_id=candidate.provider_track_id,
                    runtime_status=candidate.runtime_status,
                    rejection_reason=rejection_reason,
                )
            )

        for failure in provider_result.failures:
            diagnostics.append(
                QualityProviderDiagnostic(
                    track_id=failure.track_id,
                    track_source_id=failure.track_source_id,
                    provider=failure.provider,
                    provider_track_id=failure.provider_track_id,
                    runtime_status=failure.runtime_status,
                    rejection_reason=QualityCandidateRejectionReason.PROVIDER_NOT_AVAILABLE,
                    provider_error_code=failure.error_code,
                )
            )

        ordered_plans = tuple(sorted(plans, key=plan_sort_key))
        ordered_diagnostics = tuple(
            sorted(
                diagnostics,
                key=lambda item: (
                    item.provider.value,
                    item.provider_track_id,
                    item.track_source_id,
                ),
            )
        )
        if ordered_plans:
            status = QualityResolutionStatus.RESOLVED
        elif not any(
            item.runtime_status is ProviderRuntimeStatus.AVAILABLE for item in ordered_diagnostics
        ):
            status = QualityResolutionStatus.NO_AVAILABLE_PROVIDER
        else:
            status = QualityResolutionStatus.QUALITY_UNAVAILABLE

        resolved_at = datetime.now(UTC)
        result = QualityResolutionResult(
            track_id=track_id,
            requested_profile=quality_profile,
            status=status,
            plans=ordered_plans,
            provider_diagnostics=ordered_diagnostics,
            resolved_at=resolved_at,
        )
        for plan in result.plans:
            logger.info(
                "Quality plan resolved",
                extra={
                    "track_id": plan.track_id,
                    "requested_profile": quality_profile.value,
                    "provider": plan.provider.value,
                    "track_source_id": plan.track_source_id,
                    "plan_operation": plan.operation.value,
                    "plan_readiness": plan.readiness.value,
                },
            )
        logger.info(
            "Quality resolution completed",
            extra={
                "track_id": track_id,
                "requested_profile": quality_profile.value,
                "resolution_status": result.status.value,
            },
        )
        return result
