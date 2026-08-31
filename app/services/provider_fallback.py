"""Explicit Stage 25 failure-to-fallback policy."""

from __future__ import annotations

from enum import StrEnum

from app.core.enums import DownloadFailureCode


class FallbackDecision(StrEnum):
    SAME_ACCOUNT_RETRY = "SAME_ACCOUNT_RETRY"
    SAME_PROVIDER_NEXT_ACCOUNT = "SAME_PROVIDER_NEXT_ACCOUNT"
    NEXT_PROVIDER = "NEXT_PROVIDER"
    STOP = "STOP"


ACCOUNT_LOCAL_FAILURES = frozenset(
    {
        DownloadFailureCode.PROVIDER_AUTH,
        DownloadFailureCode.AUTH_REQUIRED,
        DownloadFailureCode.PROVIDER_RATE_LIMITED,
    }
)
PROVIDER_FALLBACK_FAILURES = frozenset(
    {
        DownloadFailureCode.MEDIA_NOT_FOUND,
        DownloadFailureCode.MEDIA_UNAVAILABLE,
        DownloadFailureCode.SOURCE_UNAVAILABLE,
        DownloadFailureCode.PROVIDER_UNAVAILABLE,
        DownloadFailureCode.PROVIDER_TEMPORARY,
        DownloadFailureCode.NETWORK,
    }
)
NO_FALLBACK_FAILURES = frozenset(
    {
        DownloadFailureCode.PROCESSING,
        DownloadFailureCode.TRANSCODE_FAILED,
        DownloadFailureCode.OUTPUT_VALIDATION_FAILED,
        DownloadFailureCode.DELIVERY_TEMPORARY,
        DownloadFailureCode.DELIVERY_PERMANENT,
        DownloadFailureCode.INTERNAL,
    }
)


def fallback_decision(
    code: DownloadFailureCode, *, another_account: bool = False, another_provider: bool = False
) -> FallbackDecision:
    if code in ACCOUNT_LOCAL_FAILURES and another_account:
        return FallbackDecision.SAME_PROVIDER_NEXT_ACCOUNT
    if code in PROVIDER_FALLBACK_FAILURES and another_provider:
        return FallbackDecision.NEXT_PROVIDER
    return FallbackDecision.STOP


__all__ = ["FallbackDecision", "fallback_decision"]
