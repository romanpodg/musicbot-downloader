"""Resolve and persist one track URL through the provider boundary."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from app.config import get_settings
from app.core.exceptions import MusicBotError
from app.logging import configure_logging
from app.providers.onthespot import OnTheSpotProvider
from app.services.track_resolution import ResolveResult, ResolveTrackService
from app.storage import Database


async def _run(url: str) -> ResolveResult:
    settings = get_settings()
    configure_logging(settings)
    database = Database(settings.database_url)
    provider = OnTheSpotProvider()
    try:
        return await ResolveTrackService(database, provider).resolve(url)
    finally:
        await provider.close()
        await database.dispose()


def _duration(duration_ms: int | None) -> str:
    if duration_ms is None:
        return "unknown"
    minutes, seconds = divmod(duration_ms // 1000, 60)
    return f"{minutes:02d}:{seconds:02d}"


def _known(value: object | None) -> str:
    return str(value) if value is not None else "unknown"


def _present(result: ResolveResult) -> str:
    metadata = result.metadata
    native = metadata.native
    return "\n".join(
        (
            "Resolved track",
            "",
            f"Track ID: {result.track.id}",
            "",
            f"Title: {_known(metadata.title)}",
            f"Artist: {_known(metadata.artist)}",
            f"Album: {_known(metadata.album)}",
            f"ISRC: {_known(metadata.isrc)}",
            f"Duration: {_duration(metadata.duration_ms)}",
            "",
            "Input provider:",
            metadata.provider.value.replace("_", " ").title(),
            "",
            "Native information:",
            f"Codec: {_known(native.codec)}",
            f"Container: {_known(native.container)}",
            f"Bitrate: {f'{native.bitrate_kbps} kbps' if native.bitrate_kbps else 'unknown'}",
            "",
            "Database:",
            f"Track: {'created' if result.track_created else 'existing'}",
            f"TrackSource: {'created' if result.source_created else 'updated'}",
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="A supported single-track URL")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(_run(args.url))
    except MusicBotError as exc:
        print(f"Resolve failed: {type(exc).__name__}")
        return 2
    print(_present(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
