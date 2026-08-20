"""Bounded, network-free local operational inspection and recovery CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, cast

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config import Settings, get_settings
from app.core.enums import (
    OperationalAuditEventType,
    QueueJobStatus,
    SubscriberStatus,
    TelegramDeliveryStatus,
)
from app.core.exceptions import ConfigurationError
from app.i18n import LocalizationService
from app.logging import configure_logging
from app.main import _schema_revision, require_current_schema
from app.services.artifact_cleanup import StaleArtifactCleanupService
from app.services.artifacts import ActiveArtifactRegistry, DownloadArtifactManager
from app.services.crash_recovery import CrashRecoveryService
from app.services.instance_lock import (
    ApplicationInstanceAlreadyRunningError,
    ApplicationInstanceLock,
)
from app.services.operational_audit import (
    ArtifactCleanupAuditDetails,
    OperationalAuditService,
)
from app.services.queues import UploadQueueService
from app.services.singleflight import SingleFlightService
from app.services.sqlite_backup import SQLiteBackupService
from app.services.telegram_album_coordinator import TelegramAlbumCoordinator
from app.storage import Database


@dataclass(frozen=True, slots=True)
class OperationalStatus:
    schema_revision: str | None
    schema_head: str | None
    schema_current: bool
    application_instance_active: bool
    download_jobs: dict[str, int]
    upload_jobs: dict[str, int]
    active_singleflight: int
    waiting_subscribers: int
    delivery_active: int
    delivery_failed: int
    active_album_requests: int
    telegram_cache_active: int
    telegram_cache_invalid: int
    desired_download_workers: int | None
    desired_upload_workers: int | None
    actual_workers: str
    temp_dir: str
    temp_free_bytes: int
    temp_reserve_bytes: int
    audit_event_count: int
    audit_latest_at: datetime | None


class _UnavailableDependency:
    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"offline recovery cannot use dependency {name}")


def _recovery_service(
    database: Database, settings: Settings, *, telegram_bot_id: int
) -> CrashRecoveryService:
    registry = ActiveArtifactRegistry()
    artifacts = DownloadArtifactManager(settings.temp_dir, registry)
    uploads = UploadQueueService(database, artifacts)
    singleflight = SingleFlightService(
        database,
        max_size=settings.queue_max_size,
        upload_queue=uploads,
    )
    unavailable = _UnavailableDependency()
    albums = TelegramAlbumCoordinator(
        database,
        cast(Any, unavailable),
        cast(Any, unavailable),
        LocalizationService(settings.supported_locales, settings.default_locale),
        album_wake_event=asyncio.Event(),
        delivery_wake_event=asyncio.Event(),
    )
    return CrashRecoveryService(
        database,
        uploads,
        singleflight,
        albums,
        telegram_bot_id=telegram_bot_id,
        delivery_max_attempts=settings.telegram_delivery_max_attempts,
    )


async def _resolve_offline_bot_id(database: Database) -> int:
    from sqlalchemy import func, select

    from app.storage.models import TelegramFileCache

    async with database.engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    select(TelegramFileCache.telegram_bot_id)
                    .group_by(TelegramFileCache.telegram_bot_id)
                    .order_by(func.count(TelegramFileCache.id).desc())
                    .limit(2)
                )
            )
            .scalars()
            .all()
        )
    return int(rows[0]) if len(rows) == 1 else 0


async def _make_recovery_service(database: Database, settings: Settings) -> CrashRecoveryService:
    # Cache rescue is bot-scoped, but an offline process must not call Telegram getMe.
    # Reuse a persisted bot ID only when the durable cache has one unambiguous identity.
    telegram_bot_id = await _resolve_offline_bot_id(database)
    return _recovery_service(database, settings, telegram_bot_id=telegram_bot_id)


async def _status(settings: Settings) -> OperationalStatus:
    database = Database(settings.database_url)
    try:
        await require_current_schema(database)
        revision = await _schema_revision(database)
        head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
        async with database.transaction() as repositories:
            download = await repositories.download_jobs.counts()
            upload = await repositories.upload_jobs.counts()
            flights = await repositories.singleflight.active_flight_count()
            subscribers = await repositories.singleflight.subscriber_counts()
            delivery = await repositories.telegram_delivery.status_counts()
            albums = await repositories.telegram_album.count_active()
            cache_active, cache_invalid, _ = await repositories.telegram_cache.stats(None)
            worker_settings = await repositories.runtime_settings.get()
            audit_count = await repositories.audit.count()
            audit_latest = await repositories.audit.latest_occurred_at()
        active_delivery_statuses = {
            TelegramDeliveryStatus.QUEUED,
            TelegramDeliveryStatus.WAITING,
            TelegramDeliveryStatus.SENDING,
        }
        return OperationalStatus(
            schema_revision=revision,
            schema_head=head,
            schema_current=revision == head,
            application_instance_active=ApplicationInstanceLock.from_database_url(
                settings.database_url
            ).probe_active(),
            download_jobs={status.value: download.get(status, 0) for status in QueueJobStatus},
            upload_jobs={status.value: upload.get(status, 0) for status in QueueJobStatus},
            active_singleflight=flights,
            waiting_subscribers=subscribers.get(SubscriberStatus.WAITING, 0),
            delivery_active=sum(delivery.get(status, 0) for status in active_delivery_statuses),
            delivery_failed=delivery.get(TelegramDeliveryStatus.FAILED, 0),
            active_album_requests=albums,
            telegram_cache_active=cache_active,
            telegram_cache_invalid=cache_invalid,
            desired_download_workers=(
                worker_settings.download_workers if worker_settings is not None else None
            ),
            desired_upload_workers=(
                worker_settings.upload_workers if worker_settings is not None else None
            ),
            actual_workers="unavailable_outside_running_process",
            temp_dir=str(settings.temp_dir.expanduser().resolve()),
            temp_free_bytes=shutil.disk_usage(settings.temp_dir).free,
            temp_reserve_bytes=settings.temp_disk_min_free_bytes,
            audit_event_count=audit_count,
            audit_latest_at=audit_latest,
        )
    finally:
        await database.dispose()


async def _audit_list(settings: Settings, args: argparse.Namespace) -> tuple[Any, ...]:
    database = Database(settings.database_url)
    try:
        await require_current_schema(database)
        selected = OperationalAuditEventType(args.event) if args.event else None
        return await OperationalAuditService(database).list_recent(
            limit=args.limit,
            before_id=args.before_id,
            event_type=selected,
            actor_user_id=args.actor_user_id,
        )
    finally:
        await database.dispose()


async def _offline_recovery(settings: Settings, *, inspect_only: bool) -> Any:
    lock = ApplicationInstanceLock.from_database_url(settings.database_url)
    lock.acquire()
    database: Database | None = None
    try:
        database = Database(settings.database_url)
        await require_current_schema(database)
        recovery = await _make_recovery_service(database, settings)
        return (
            await recovery.inspect()
            if inspect_only
            else await recovery.recover_startup(manual=True)
        )
    finally:
        if database is not None:
            await database.dispose()
        lock.release()


async def _offline_artifacts(settings: Settings, *, scan_only: bool) -> Any:
    lock = ApplicationInstanceLock.from_database_url(settings.database_url)
    lock.acquire()
    database: Database | None = None
    try:
        database = Database(settings.database_url)
        await require_current_schema(database)
        service = StaleArtifactCleanupService(
            database,
            settings.temp_dir,
            ActiveArtifactRegistry(),
            stale_after_seconds=settings.temp_artifact_stale_after_seconds,
        )
        summary = await service.scan() if scan_only else await service.sweep()
        if not scan_only:
            await OperationalAuditService(database).append_artifact_cleanup(
                ArtifactCleanupAuditDetails(**asdict(summary))
            )
        return summary
    finally:
        if database is not None:
            await database.dispose()
        lock.release()


async def _backup(settings: Settings, destination: Path) -> Any:
    database = Database(settings.database_url)
    try:
        await require_current_schema(database)
        return await SQLiteBackupService(database).create(destination)
    finally:
        await database.dispose()


def _normalized(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _normalized(asdict(value))
    if isinstance(value, dict):
        return {str(key): _normalized(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalized(item) for item in value]
    if isinstance(value, (datetime, Path)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    if isinstance(value, Enum):
        return value.value
    return value


def _print(value: Any, *, json_output: bool) -> None:
    normalized = _normalized(value)
    if json_output:
        print(json.dumps(normalized, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
        return
    if isinstance(normalized, list):
        if not normalized:
            print("No matching records.")
            return
        for item in normalized:
            assert isinstance(item, dict)
            details = json.dumps(item.pop("details", {}), sort_keys=True, separators=(",", ":"))
            print(" ".join(f"{key}={value}" for key, value in item.items()), f"details={details}")
        return
    assert isinstance(normalized, dict)
    for key, item in normalized.items():
        if isinstance(item, dict):
            print(f"{key}: " + " ".join(f"{name}={count}" for name, count in item.items()))
        else:
            print(f"{key}: {item}")


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit stable compact JSON")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local operational tooling")
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status", help="inspect bounded durable/local state")
    _add_json(status)

    audit = commands.add_parser("audit", help="inspect append-only operational audit")
    audit_commands = audit.add_subparsers(dest="audit_command", required=True)
    audit_list = audit_commands.add_parser("list", help="list recent audit events")
    audit_list.add_argument("--limit", type=int, default=50, choices=range(1, 201))
    audit_list.add_argument("--before-id", type=int)
    audit_list.add_argument("--actor-user-id", type=int)
    audit_list.add_argument("--event", choices=[event.value for event in OperationalAuditEventType])
    _add_json(audit_list)

    recovery = commands.add_parser("recovery", help="inspect or run crash recovery offline")
    recovery_commands = recovery.add_subparsers(dest="recovery_command", required=True)
    recovery_inspect = recovery_commands.add_parser("inspect", help="dry-run recovery snapshot")
    _add_json(recovery_inspect)
    recovery_run = recovery_commands.add_parser("run", help="execute recovery while app is stopped")
    _add_json(recovery_run)

    artifacts = commands.add_parser(
        "artifacts", help="inspect or clean configured TEMP_DIR offline"
    )
    artifact_commands = artifacts.add_subparsers(dest="artifact_command", required=True)
    artifact_scan = artifact_commands.add_parser("scan", help="dry-run stale artifact policy")
    _add_json(artifact_scan)
    artifact_cleanup = artifact_commands.add_parser(
        "cleanup", help="run one safe cleanup sweep while app is stopped"
    )
    _add_json(artifact_cleanup)

    backup = commands.add_parser("backup", help="create a validated online SQLite backup")
    backup_commands = backup.add_subparsers(dest="backup_command", required=True)
    backup_create = backup_commands.add_parser("create", help="create without overwriting")
    backup_create.add_argument("destination", type=Path)
    _add_json(backup_create)
    return parser


async def _dispatch(settings: Settings, args: argparse.Namespace) -> Any:
    if args.command == "status":
        return await _status(settings)
    if args.command == "audit":
        return await _audit_list(settings, args)
    if args.command == "recovery":
        return await _offline_recovery(settings, inspect_only=args.recovery_command == "inspect")
    if args.command == "artifacts":
        return await _offline_artifacts(settings, scan_only=args.artifact_command == "scan")
    if args.command == "backup":
        return await _backup(settings, args.destination)
    raise RuntimeError("unsupported operation")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = get_settings()
    except ConfigurationError:
        print("Application configuration is invalid.", file=sys.stderr)
        return 2
    configure_logging(settings)
    try:
        result = asyncio.run(_dispatch(settings, args))
        _print(result, json_output=bool(args.json))
        return 0
    except ApplicationInstanceAlreadyRunningError:
        print("Operation requires the application instance to be stopped.", file=sys.stderr)
        return 2
    except (ConfigurationError, FileExistsError, ValueError, OSError) as exc:
        print(f"Operation refused: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("Operation failed; consult sanitized application logs.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
