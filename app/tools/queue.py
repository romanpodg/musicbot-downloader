"""Developer CLI for persistent Stage 7 queue inspection and settings."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import asdict

from app.config import get_settings
from app.core.enums import QualityProfile
from app.services.artifacts import DownloadArtifactManager
from app.services.queues import DownloadQueueService, UploadQueueService, WorkerSettingsService
from app.storage import Database


async def _run(args: argparse.Namespace) -> str:
    settings = get_settings()
    database = Database(settings.database_url)
    downloads = DownloadQueueService(database, max_size=settings.queue_max_size)
    uploads = UploadQueueService(database, DownloadArtifactManager(settings.temp_dir))
    workers = WorkerSettingsService(database, settings)
    try:
        if args.command == "submit":
            job = await downloads.submit(
                track_id=args.track_id,
                quality_profile=QualityProfile(args.quality_profile),
            )
            return _json(asdict(job))
        if args.command == "jobs":
            jobs = (
                await downloads.list_download_jobs(offset=args.offset, limit=args.limit)
                if args.queue == "download"
                else await uploads.list_upload_jobs(offset=args.offset, limit=args.limit)
            )
            return _json([asdict(job) for job in jobs])
        if args.command == "workers":
            if args.pool is not None:
                if args.pool == "download":
                    await workers.set_download_workers(args.count)
                else:
                    await workers.set_upload_workers(args.count)
            return _json(asdict(await workers.get_values()))
        if args.command == "status":
            return _json(
                {
                    "workers": asdict(await workers.get_values()),
                    "download_jobs": asdict(await downloads.counts()),
                    "upload_jobs": asdict(await uploads.counts()),
                    "actual_workers": {"download": 0, "upload": 0},
                    "processing": "not_started_by_inspection_cli",
                }
            )
        raise AssertionError("unknown command")
    finally:
        await database.dispose()


def _json(value: object) -> str:
    return json.dumps(value, indent=2, default=str)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect Stage 7 queues and persisted runtime worker settings."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit = subparsers.add_parser("submit", help="persist a download job")
    submit.add_argument("track_id", type=int)
    submit.add_argument("quality_profile", choices=[profile.value for profile in QualityProfile])

    subparsers.add_parser("status", help="show queue counts and worker settings")

    jobs = subparsers.add_parser("jobs", help="show bounded persistent job history")
    jobs.add_argument("queue", choices=("download", "upload"), nargs="?", default="download")
    jobs.add_argument("--offset", type=int, default=0)
    jobs.add_argument("--limit", type=int, default=50)

    worker_parser = subparsers.add_parser("workers", help="inspect or change desired workers")
    worker_parser.add_argument("pool", choices=("download", "upload"), nargs="?")
    worker_parser.add_argument("count", type=int, nargs="?")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "workers" and (args.pool is None) != (args.count is None):
        parser.error("workers requires both POOL and COUNT, or neither")
    print(asyncio.run(_run(args)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
