"""Pure application-search readiness evaluation without network side effects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.core.enums import ProviderHealthStatus
from app.core.models import ProviderCapabilities, ProviderHealthEntry
from app.provider_integration import ProviderIntegrationSpec, ProviderSearchIntegrationState


class ProviderSearchReadinessStatus(StrEnum):
    READY = "READY"
    NOT_INTEGRATED = "NOT_INTEGRATED"
    UNSUPPORTED = "UNSUPPORTED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class ProviderSearchReadiness:
    status: ProviderSearchReadinessStatus
    reason_code: str


def evaluate_provider_search_readiness(
    integration: ProviderIntegrationSpec,
    capabilities: ProviderCapabilities,
    *,
    runtime_searchable: bool,
    health: ProviderHealthEntry | None,
) -> ProviderSearchReadiness:
    """Evaluate sanitized facts; provider health never stands in for source checks."""

    if not capabilities.search_supported:
        return ProviderSearchReadiness(
            ProviderSearchReadinessStatus.UNSUPPORTED, "static_unsupported"
        )
    if integration.search is ProviderSearchIntegrationState.DEFERRED:
        return ProviderSearchReadiness(ProviderSearchReadinessStatus.NOT_INTEGRATED, "deferred")
    if runtime_searchable:
        return ProviderSearchReadiness(ProviderSearchReadinessStatus.READY, "runtime_searchable")
    if health is None:
        return ProviderSearchReadiness(
            ProviderSearchReadinessStatus.UNKNOWN, "runtime_evidence_missing"
        )
    return ProviderSearchReadiness(
        {
            ProviderHealthStatus.AUTH_REQUIRED: ProviderSearchReadinessStatus.AUTH_REQUIRED,
            ProviderHealthStatus.UNAVAILABLE: ProviderSearchReadinessStatus.UNAVAILABLE,
            ProviderHealthStatus.UNKNOWN: ProviderSearchReadinessStatus.UNKNOWN,
            ProviderHealthStatus.ERROR: ProviderSearchReadinessStatus.ERROR,
            ProviderHealthStatus.READY: ProviderSearchReadinessStatus.UNAVAILABLE,
        }[health.status],
        "runtime_not_searchable"
        if health.status is ProviderHealthStatus.READY
        else health.status.value.lower(),
    )
