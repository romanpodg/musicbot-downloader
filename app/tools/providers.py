"""Inspect runtime provider candidates for one canonical Track."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from app.config import get_settings
from app.core.exceptions import MusicBotError
from app.core.models import DownloadProviderCandidate, ProviderCandidateFailure
from app.logging import configure_logging
from app.providers.onthespot import OnTheSpotProvider
from app.services.provider_resolution import ProviderResolver
from app.storage import Database


async def _run(track_id: int) -> str:
    settings = get_settings()
    configure_logging(settings)
    database = Database(settings.database_url)
    provider = OnTheSpotProvider()
    try:
        result = await ProviderResolver(database, provider).resolve(track_id)
    finally:
        await provider.close()
        await database.dispose()

    lines = [f"Canonical Track: {result.track_id}", f"Result: {result.status.value}", ""]
    lines.append("Available candidates:")
    if not result.candidates:
        lines.append("  (none)")
    for candidate in result.candidates:
        lines.extend(_candidate_lines(candidate))
    lines.append("")
    lines.append("Unavailable sources:")
    if not result.failures:
        lines.append("  (none)")
    for failure in result.failures:
        lines.extend(_failure_lines(failure))
    return "\n".join(lines)


def _candidate_lines(candidate: DownloadProviderCandidate) -> list[str]:
    native = candidate.native_media_info
    native_parts: list[str] = []
    if native is not None:
        if native.codec is not None:
            native_parts.append(native.codec.value)
        if native.container is not None:
            native_parts.append(native.container.value)
        if native.bitrate_kbps is not None:
            native_parts.append(f"{native.bitrate_kbps} kbps")
    native_text = ", ".join(native_parts) or "unknown"
    return [
        f"  {candidate.provider.value}",
        f"    track_source_id: {candidate.track_source_id}",
        f"    runtime: {candidate.runtime_status.value}",
        f"    native: {native_text}",
    ]


def _failure_lines(failure: ProviderCandidateFailure) -> list[str]:
    lines = [
        f"  {failure.provider.value}",
        f"    track_source_id: {failure.track_source_id}",
        f"    runtime: {failure.runtime_status.value}",
    ]
    if failure.error_code is not None:
        lines.append(f"    code: {failure.error_code}")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("track_id", type=int, help="Canonical Track ID")
    args = parser.parse_args(argv)
    if args.track_id <= 0:
        parser.error("track_id must be positive")
    try:
        print(asyncio.run(_run(args.track_id)))
    except MusicBotError as exc:
        print(f"Provider resolution failed: {type(exc).__name__}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
