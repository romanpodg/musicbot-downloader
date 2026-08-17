"""Provider-neutral quality policy for future download planning."""

from __future__ import annotations

from typing import Final

from app.core.enums import (
    DownloadPlanOperation,
    DownloadPlanReadiness,
    DownloadPlanReason,
    NativeCodec,
    NativeContainer,
    QualityCandidateRejectionReason,
    QualityProfile,
)
from app.core.models import (
    DownloadPlan,
    DownloadProviderCandidate,
    NativeMediaInfo,
    OutputSpecification,
    SourceMediaRequirement,
)

QUALITY_OUTPUTS: Final[dict[QualityProfile, OutputSpecification]] = {
    QualityProfile.MP3_128: OutputSpecification(
        codec=NativeCodec.MP3,
        container=NativeContainer.MP3,
        bitrate_kbps=128,
        lossless=False,
    ),
    QualityProfile.MP3_320: OutputSpecification(
        codec=NativeCodec.MP3,
        container=NativeContainer.MP3,
        bitrate_kbps=320,
        lossless=False,
    ),
    QualityProfile.AAC_256: OutputSpecification(
        codec=NativeCodec.AAC,
        container=NativeContainer.M4A,
        bitrate_kbps=256,
        lossless=False,
    ),
    QualityProfile.LOSSLESS: OutputSpecification(
        codec=None,
        container=None,
        bitrate_kbps=None,
        lossless=True,
        preserve_source=True,
    ),
}


def plans_for_candidate(
    candidate: DownloadProviderCandidate,
    requested_profile: QualityProfile,
) -> tuple[DownloadPlan, ...]:
    """Build every safe strategy for one currently available source."""

    native = candidate.native_media_info
    if native is not None and native.codec not in {None, NativeCodec.UNKNOWN}:
        confirmed = _plan_for_media(
            candidate,
            requested_profile,
            native,
            DownloadPlanReadiness.CONFIRMED,
        )
        return (confirmed,) if confirmed is not None else ()

    plans: list[DownloadPlan] = []
    for media in candidate.capabilities.media.potential_media:
        plan = _plan_for_media(
            candidate,
            requested_profile,
            media,
            DownloadPlanReadiness.REQUIRES_PREFLIGHT,
        )
        if plan is not None and _strategy_identity(plan) not in {
            _strategy_identity(existing) for existing in plans
        }:
            plans.append(plan)
    return tuple(plans)


def rejection_reason_for_candidate(
    candidate: DownloadProviderCandidate,
    requested_profile: QualityProfile,
) -> QualityCandidateRejectionReason:
    """Return one deterministic, machine-readable explanation for no plan."""

    native = candidate.native_media_info
    if native is None or native.codec in {None, NativeCodec.UNKNOWN}:
        media = candidate.capabilities.media
        if not media.known or not media.potential_media:
            return QualityCandidateRejectionReason.SOURCE_MEDIA_UNKNOWN
        if requested_profile is QualityProfile.LOSSLESS and not any(
            _is_lossless(item) for item in media.potential_media
        ):
            return QualityCandidateRejectionReason.LOSSLESS_REQUIRED
        target = QUALITY_OUTPUTS[requested_profile]
        if target.codec is not None and any(
            item.codec is target.codec
            and item.bitrate_kbps is not None
            and target.bitrate_kbps is not None
            and item.bitrate_kbps < target.bitrate_kbps
            for item in media.potential_media
        ):
            return QualityCandidateRejectionReason.UPSCALE_FORBIDDEN
        return QualityCandidateRejectionReason.LOSSY_TRANSCODE_FORBIDDEN

    if requested_profile is QualityProfile.LOSSLESS:
        return QualityCandidateRejectionReason.LOSSLESS_REQUIRED

    target = QUALITY_OUTPUTS[requested_profile]
    if native.codec is target.codec:
        if native.bitrate_kbps is None:
            return QualityCandidateRejectionReason.SOURCE_MEDIA_UNKNOWN
        if target.bitrate_kbps is not None and native.bitrate_kbps < target.bitrate_kbps:
            return QualityCandidateRejectionReason.UPSCALE_FORBIDDEN
        if target.bitrate_kbps is not None and native.bitrate_kbps > target.bitrate_kbps:
            return QualityCandidateRejectionReason.LOSSY_TRANSCODE_FORBIDDEN
        return QualityCandidateRejectionReason.TARGET_BITRATE_MISMATCH
    return QualityCandidateRejectionReason.LOSSY_TRANSCODE_FORBIDDEN


def plan_sort_key(plan: DownloadPlan) -> tuple[int, str, str, int, str]:
    """Rank semantic quality/certainty first, then stable neutral identities."""

    tier = {
        (DownloadPlanOperation.DIRECT, DownloadPlanReadiness.CONFIRMED): 0,
        (DownloadPlanOperation.DIRECT, DownloadPlanReadiness.REQUIRES_PREFLIGHT): 1,
        (DownloadPlanOperation.TRANSCODE, DownloadPlanReadiness.CONFIRMED): 2,
        (DownloadPlanOperation.TRANSCODE, DownloadPlanReadiness.REQUIRES_PREFLIGHT): 3,
    }[(plan.operation, plan.readiness)]
    return (
        tier,
        plan.provider.value,
        plan.provider_track_id,
        plan.track_source_id,
        plan.source_expectation.required_codec.value
        if plan.source_expectation.required_codec is not None
        else "",
    )


def _plan_for_media(
    candidate: DownloadProviderCandidate,
    requested_profile: QualityProfile,
    media: NativeMediaInfo,
    readiness: DownloadPlanReadiness,
) -> DownloadPlan | None:
    output = QUALITY_OUTPUTS[requested_profile]
    if _is_lossless(media) and (
        readiness is DownloadPlanReadiness.CONFIRMED
        or candidate.capabilities.media.supports_lossless is True
    ):
        operation = (
            DownloadPlanOperation.DIRECT if output.lossless else DownloadPlanOperation.TRANSCODE
        )
        requirement = SourceMediaRequirement(required_lossless=True)
        confirmed_reason = (
            DownloadPlanReason.NATIVE_EXACT_MATCH
            if operation is DownloadPlanOperation.DIRECT
            else DownloadPlanReason.LOSSLESS_TO_REQUESTED_LOSSY
        )
    elif (
        not output.lossless
        and media.codec is output.codec
        and media.bitrate_kbps == output.bitrate_kbps
    ):
        operation = DownloadPlanOperation.DIRECT
        requirement = SourceMediaRequirement(
            required_codec=output.codec,
            required_bitrate_kbps=output.bitrate_kbps,
        )
        confirmed_reason = DownloadPlanReason.NATIVE_EXACT_MATCH
    else:
        return None

    reason = (
        confirmed_reason
        if readiness is DownloadPlanReadiness.CONFIRMED
        else DownloadPlanReason.PROVIDER_PREFLIGHT_REQUIRED
    )
    return DownloadPlan(
        track_id=candidate.track_id,
        track_source_id=candidate.track_source_id,
        provider=candidate.provider,
        provider_track_id=candidate.provider_track_id,
        requested_profile=requested_profile,
        source_expectation=requirement,
        output_specification=output,
        operation=operation,
        readiness=readiness,
        reason=reason,
    )


def _is_lossless(media: NativeMediaInfo) -> bool:
    return media.codec is NativeCodec.FLAC


def _strategy_identity(
    plan: DownloadPlan,
) -> tuple[DownloadPlanOperation, SourceMediaRequirement]:
    return plan.operation, plan.source_expectation
