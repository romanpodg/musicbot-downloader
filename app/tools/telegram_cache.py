"""Developer CLI for Stage 8 Telegram cache inspection and invalidation."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import asdict

from app.config import get_settings
from app.core.enums import QualityProfile, TelegramCacheStatus
from app.core.models import CachedTelegramFile
from app.services.telegram_cache import TelegramFileCacheService
from app.storage import Database
from app.telegram import AiogramTelegramGateway


async def _current_bot_id() -> int:
    settings = get_settings()
    token, _ = settings.telegram_cache_configuration()
    gateway = AiogramTelegramGateway(token)
    try:
        return (await gateway.get_bot_identity()).telegram_bot_id
    finally:
        await gateway.close()


async def _run(args: argparse.Namespace) -> str:
    settings = get_settings()
    if args.command == "verify-gateway":
        token, cache_chat_id = settings.telegram_cache_configuration()
        gateway = AiogramTelegramGateway(token)
        try:
            identity = await gateway.get_bot_identity()
            return _json(
                {
                    "telegram_bot_id": identity.telegram_bot_id,
                    "username": identity.username,
                    "cache_chat_id": cache_chat_id,
                    "status": "OK",
                }
            )
        finally:
            await gateway.close()

    database = Database(settings.database_url)
    cache = TelegramFileCacheService(database)
    try:
        if args.command == "status":
            return _json(asdict(await cache.stats(telegram_bot_id=args.bot_id)))
        if args.command == "list":
            entries = await cache.list_entries(
                offset=args.offset,
                limit=args.limit,
                status=(TelegramCacheStatus(args.status) if args.status else None),
                track_id=args.track_id,
                telegram_bot_id=args.bot_id,
            )
            return _json([_safe_entry(entry, args.show_file_id) for entry in entries])
        if args.command == "get":
            bot_id = args.bot_id if args.bot_id is not None else await _current_bot_id()
            entry = await cache.get_active(
                telegram_bot_id=bot_id,
                track_id=args.track_id,
                quality_profile=QualityProfile(args.quality_profile),
            )
            return _json(None if entry is None else _safe_entry(entry, args.show_file_id))
        if args.command == "invalidate":
            entry = await cache.invalidate(args.cache_id, reason_code=args.reason_code)
            return _json(_safe_entry(entry, False))
        raise AssertionError("unknown command")
    finally:
        await database.dispose()


def _safe_entry(entry: CachedTelegramFile, show_file_id: bool) -> dict[str, object]:
    values: dict[str, object] = asdict(entry)
    file_id = str(values.pop("file_id"))
    values["file_id"] = file_id if show_file_id else _redact(file_id)
    return values


def _redact(value: str) -> str:
    if len(value) <= 8:
        return "[REDACTED]"
    return f"{value[:4]}...{value[-4:]}"


def _json(value: object) -> str:
    return json.dumps(value, indent=2, default=str)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect and invalidate the durable Stage 8 Telegram file cache."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="show cache entry and media-byte counts")
    status.add_argument("--bot-id", type=int)

    listing = subparsers.add_parser("list", help="list bounded cache metadata")
    listing.add_argument("--offset", type=int, default=0)
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--status", choices=[status.value for status in TelegramCacheStatus])
    listing.add_argument("--track-id", type=int)
    listing.add_argument("--bot-id", type=int)
    listing.add_argument("--show-file-id", action="store_true")

    get = subparsers.add_parser("get", help="look up an exact Track + QualityProfile")
    get.add_argument("track_id", type=int)
    get.add_argument("quality_profile", choices=[profile.value for profile in QualityProfile])
    get.add_argument("--bot-id", type=int)
    get.add_argument("--show-file-id", action="store_true")

    invalidate = subparsers.add_parser("invalidate", help="mark one cache row INVALID")
    invalidate.add_argument("cache_id", type=int)
    invalidate.add_argument("--reason-code")

    subparsers.add_parser(
        "verify-gateway", help="validate configuration and resolve the bot identity with getMe"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    print(asyncio.run(_run(_parser().parse_args(argv))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
