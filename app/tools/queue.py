"""Developer CLI for Stage 7 queues and Stage 7.1 subscribers."""

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
from app.services.singleflight import SingleFlightService, SubscriberNotifier
from app.storage import Database


async def _run(args: argparse.Namespace) -> str:
    settings = get_settings()
    database = Database(settings.database_url)
    notifier = SubscriberNotifier()
    artifacts = DownloadArtifactManager(settings.temp_dir)
    downloads = DownloadQueueService(
        database, max_size=settings.queue_max_size, subscriber_notifier=notifier
    )
    uploads = UploadQueueService(database, artifacts, subscriber_notifier=notifier)
    singleflight = SingleFlightService(
        database,
        max_size=settings.queue_max_size,
        notifier=notifier,
        upload_queue=uploads,
    )
    workers = WorkerSettingsService(database, settings)
    try:
        await singleflight.reconcile()
        if args.command == "submit":
            submission = await singleflight.submit(
                track_id=args.track_id,
                quality_profile=QualityProfile(args.quality_profile),
                request_key=args.request_key,
            )
            return _json(asdict(submission))
        if args.command == "subscriber":
            return _json(asdict(await singleflight.get_subscriber(args.subscriber_id)))
        if args.command == "subscribers":
            subscribers = await singleflight.list_job_subscribers(
                args.download_job_id, offset=args.offset, limit=args.limit
            )
            return _json([asdict(subscriber) for subscriber in subscribers])
        if args.command == "cancel-subscriber":
            return _json(asdict(await singleflight.cancel_subscriber(args.subscriber_id)))
        if args.command == "wait":
            return _json(asdict(await singleflight.wait(args.subscriber_id, timeout=args.timeout)))
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
                    "singleflight": asdict(await singleflight.snapshot()),
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
        description="Inspect Stage 7 queues, SingleFlight subscribers, and worker settings."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit = subparsers.add_parser("submit", help="submit or join shared active work")
    submit.add_argument("track_id", type=int)
    submit.add_argument("quality_profile", choices=[profile.value for profile in QualityProfile])
    submit.add_argument("--request-key", help="opaque idempotency key for this active flight")

    subscriber = subparsers.add_parser("subscriber", help="inspect one persistent subscriber")
    subscriber.add_argument("subscriber_id")

    subscribers = subparsers.add_parser(
        "subscribers", help="list bounded subscribers for a download job"
    )
    subscribers.add_argument("download_job_id", type=int)
    subscribers.add_argument("--offset", type=int, default=0)
    subscribers.add_argument("--limit", type=int, default=50)

    cancel_subscriber = subparsers.add_parser("cancel-subscriber", help="cancel one subscriber")
    cancel_subscriber.add_argument("subscriber_id")

    wait = subparsers.add_parser("wait", help="wait for a subscriber terminal state")
    wait.add_argument("subscriber_id")
    wait.add_argument("--timeout", type=float)

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
