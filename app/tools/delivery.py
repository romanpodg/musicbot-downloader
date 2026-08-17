"""Developer CLI for the Stage 8 cache-first delivery admission path."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import asdict

from app.composition import compose_stage8
from app.config import get_settings
from app.core.enums import QualityProfile
from app.storage import Database


async def _run(args: argparse.Namespace) -> str:
    settings = get_settings()
    database = Database(settings.database_url)
    components = await compose_stage8(database, settings)
    try:
        result = await components.delivery_preparation.prepare(
            track_id=args.track_id,
            quality_profile=QualityProfile(args.quality_profile),
            request_key=args.request_key,
        )
        values = asdict(result)
        cached = values.get("cached_file")
        if isinstance(cached, dict) and cached.get("file_id"):
            file_id = str(cached["file_id"])
            cached["file_id"] = (
                "[REDACTED]" if len(file_id) <= 8 else f"{file_id[:4]}...{file_id[-4:]}"
            )
        return json.dumps(values, indent=2, default=str)
    finally:
        await components.close()
        await database.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare a cache-first Stage 8 delivery.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="return CACHE_HIT or PENDING")
    prepare.add_argument("track_id", type=int)
    prepare.add_argument("quality_profile", choices=[profile.value for profile in QualityProfile])
    prepare.add_argument("--request-key")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    print(asyncio.run(_run(_parser().parse_args(argv))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
