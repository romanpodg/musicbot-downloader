"""Execute and validate one Stage 6 download plan set for development."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from app.config import get_settings
from app.core.enums import QualityProfile
from app.core.exceptions import DownloadPipelineError, MusicBotError
from app.core.models import DownloadResult
from app.logging import configure_logging
from app.providers.onthespot import OnTheSpotProvider
from app.services.artifacts import DownloadArtifactManager
from app.services.download_pipeline import DownloadPipeline
from app.services.media import MediaProbe, Transcoder
from app.services.provider_resolution import ProviderResolver
from app.services.quality_resolution import QualityResolver
from app.storage import Database


async def _run(
    track_id: int, quality_profile: QualityProfile, output: Path | None
) -> tuple[DownloadResult, Path | None]:
    settings = get_settings()
    configure_logging(settings)
    database = Database(settings.database_url)
    provider = OnTheSpotProvider()
    artifacts = DownloadArtifactManager(settings.temp_dir)
    pipeline = DownloadPipeline(
        database,
        QualityResolver(ProviderResolver(database, provider)),
        provider,
        artifacts,
        MediaProbe(settings.temp_dir, settings.ffprobe_binary),
        Transcoder(
            settings.temp_dir,
            settings.ffmpeg_binary,
            timeout=settings.transcode_timeout_seconds,
        ),
        download_timeout=settings.download_timeout_seconds,
    )
    result: DownloadResult | None = None
    copied: Path | None = None
    try:
        result = await pipeline.download(track_id, quality_profile)
        if output is not None:
            copied = _copy_output(result.file_path, output)
        return result, copied
    finally:
        if result is not None:
            artifacts.release(result.job_id)
        await provider.close()
        await database.dispose()


def _copy_output(source: Path, output: Path) -> Path:
    destination = output.expanduser().resolve()
    if destination.exists():
        raise FileExistsError(destination)
    if not destination.parent.is_dir():
        raise FileNotFoundError(destination.parent)
    partial = destination.with_name(destination.name + ".partial")
    try:
        shutil.copy2(source, partial)
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def _present(result: DownloadResult, copied: Path | None) -> str:
    source = result.source_media
    output = result.output_media
    attempts = [
        f"  {item.plan_rank}. {item.provider.value}: {item.status.value}"
        + (f" ({item.failure_code.value})" if item.failure_code else "")
        for item in result.attempts
    ]
    return "\n".join(
        [
            "Download complete",
            "",
            f"Track: {result.track_id}",
            f"Requested: {result.requested_profile.value}",
            f"Provider: {result.provider.value}",
            f"TrackSource: {result.track_source_id}",
            f"Plan: {result.operation.value} / {result.plan_readiness.value}",
            f"Source: {_media(source)}",
            f"Output: {_media(output)}",
            f"Size: {result.file_size} bytes",
            "Attempts:",
            *attempts,
            f"Copied output: {copied}" if copied else "Temporary artifacts: cleaned",
        ]
    )


def _media(media: object) -> str:
    codec = getattr(media, "codec", None)
    container = getattr(media, "container", None)
    bitrate = getattr(media, "bitrate_kbps", None)
    return (
        f"{codec.value if codec else 'unknown'} / "
        f"{container.value if container else 'unknown'} / "
        f"{f'{bitrate} kbps' if bitrate else 'unknown bitrate'}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("track_id", type=int, help="Canonical Track ID")
    parser.add_argument(
        "quality_profile",
        type=QualityProfile,
        choices=list(QualityProfile),
        help="Requested delivery quality",
    )
    parser.add_argument("--output", type=Path, help="Explicit developer output file")
    args = parser.parse_args(argv)
    if args.track_id <= 0:
        parser.error("track_id must be positive")
    try:
        result, copied = asyncio.run(_run(args.track_id, args.quality_profile, args.output))
    except DownloadPipelineError as exc:
        code = getattr(exc.code, "value", str(exc.code))
        print(f"Download failed: {code}")
        return 2
    except (MusicBotError, OSError) as exc:
        print(f"Download failed: {type(exc).__name__}")
        return 2
    print(_present(result, copied))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
