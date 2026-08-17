"""Plan quality-safe future downloads for one canonical Track."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from app.config import get_settings
from app.core.enums import QualityProfile
from app.core.exceptions import MusicBotError
from app.core.models import DownloadPlan, QualityResolutionResult
from app.logging import configure_logging
from app.providers.onthespot import OnTheSpotProvider
from app.services.provider_resolution import ProviderResolver
from app.services.quality_resolution import QualityResolver
from app.storage import Database


async def _run(track_id: int, quality_profile: QualityProfile) -> QualityResolutionResult:
    settings = get_settings()
    configure_logging(settings)
    database = Database(settings.database_url)
    provider = OnTheSpotProvider()
    try:
        provider_resolver = ProviderResolver(database, provider)
        return await QualityResolver(provider_resolver).resolve(track_id, quality_profile)
    finally:
        await provider.close()
        await database.dispose()


def _present(result: QualityResolutionResult) -> str:
    lines = [
        f"Track: {result.track_id}",
        f"Requested: {result.requested_profile.value}",
        f"Result: {result.status.value}",
        f"Resolved at: {result.resolved_at.isoformat()}",
        "",
        "Provider runtime:",
    ]
    if not result.provider_diagnostics:
        lines.append("  (none)")
    for diagnostic in result.provider_diagnostics:
        lines.append(f"  {diagnostic.provider.value}: {diagnostic.runtime_status.value}")

    lines.extend(("", "Quality plans:"))
    if not result.plans:
        lines.append("  (none)")
    for index, plan in enumerate(result.plans, 1):
        lines.extend(_plan_lines(index, plan))

    rejected = [
        diagnostic
        for diagnostic in result.provider_diagnostics
        if diagnostic.rejection_reason is not None
    ]
    lines.extend(("", "Rejected:"))
    if not rejected:
        lines.append("  (none)")
    for diagnostic in rejected:
        rejection_reason = diagnostic.rejection_reason
        assert rejection_reason is not None
        lines.append(f"  {diagnostic.provider.value}: {rejection_reason.value}")
        if diagnostic.provider_error_code is not None:
            lines.append(f"    provider code: {diagnostic.provider_error_code}")
    return "\n".join(lines)


def _plan_lines(index: int, plan: DownloadPlan) -> list[str]:
    source = plan.source_expectation
    output = plan.output_specification
    requirement_parts: list[str] = []
    if source.required_codec is not None:
        requirement_parts.append(f"codec={source.required_codec.value}")
    if source.required_bitrate_kbps is not None:
        requirement_parts.append(f"bitrate={source.required_bitrate_kbps} kbps")
    if source.required_lossless is not None:
        requirement_parts.append(f"lossless={str(source.required_lossless).lower()}")

    if output.lossless:
        output_text = "genuine source lossless (preserved)"
    else:
        output_text = (
            f"{output.codec.value if output.codec else 'unknown'} / "
            f"{output.container.value if output.container else 'unknown'} / "
            f"{output.bitrate_kbps} kbps"
        )
    return [
        f"  {index}. {plan.provider.value}",
        f"     track_source_id: {plan.track_source_id}",
        f"     readiness: {plan.readiness.value}",
        f"     operation: {plan.operation.value}",
        f"     source requirement: {', '.join(requirement_parts)}",
        f"     output: {output_text}",
        f"     reason: {plan.reason.value}",
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("track_id", type=int, help="Canonical Track ID")
    parser.add_argument(
        "quality_profile",
        type=QualityProfile,
        choices=list(QualityProfile),
        help="Requested delivery quality",
    )
    args = parser.parse_args(argv)
    if args.track_id <= 0:
        parser.error("track_id must be positive")
    try:
        result = asyncio.run(_run(args.track_id, args.quality_profile))
    except MusicBotError as exc:
        print(f"Quality resolution failed: {type(exc).__name__}")
        return 2
    print(_present(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
