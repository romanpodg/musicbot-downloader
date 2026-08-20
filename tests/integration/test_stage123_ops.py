from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from alembic.config import Config

from alembic import command
from app.config import Settings, get_settings
from app.core.enums import OperationalAuditActorKind, OperationalAuditEventType
from app.services.instance_lock import (
    ApplicationInstanceAlreadyRunningError,
    ApplicationInstanceLock,
)
from app.services.operational_audit import OperationalAuditService
from app.services.sqlite_backup import SQLiteBackupService
from app.storage import Database
from app.tools.ops import _make_recovery_service, _offline_artifacts, _offline_recovery, _status


async def _migrate(path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite+aiosqlite:///{path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "head")
    return url


async def test_online_backup_is_valid_live_safe_and_non_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.db"
    url = await _migrate(source, monkeypatch)
    with sqlite3.connect(source) as connection:
        connection.execute(
            "INSERT INTO tracks (id, title, created_at, updated_at) "
            "VALUES (1, 'Representative', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )

    database = Database(url)
    runtime_lock = ApplicationInstanceLock.from_database_url(url)
    runtime_lock.acquire()
    try:
        destination = tmp_path / "backup.db"
        result = await SQLiteBackupService(database).create(destination)
        assert result.destination == destination
        assert result.schema_revision == "20260820_0011"
        with sqlite3.connect(destination) as backup:
            assert backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert backup.execute("SELECT title FROM tracks WHERE id=1").fetchone() == (
                "Representative",
            )
            # The successful-backup event is intentionally written after the snapshot.
            assert backup.execute("SELECT count(*) FROM operational_audit_events").fetchone() == (
                0,
            )
        events = await OperationalAuditService(database).list_recent(limit=10)
        assert events[0].event_type is OperationalAuditEventType.SQLITE_BACKUP_CREATED
        assert events[0].details["filename"] == "backup.db"
        with pytest.raises(FileExistsError):
            await SQLiteBackupService(database).create(destination)
        with pytest.raises(ValueError, match="differ"):
            await SQLiteBackupService(database).create(source)
        if os.name != "nt":
            assert destination.stat().st_mode & 0o077 == 0
            symlink_target = tmp_path / "symlink-target.db"
            symlink_target.write_bytes(b"must survive")
            symlink_destination = tmp_path / "symlink-backup.db"
            symlink_destination.symlink_to(symlink_target)
            with pytest.raises(FileExistsError):
                await SQLiteBackupService(database).create(symlink_destination)
            assert symlink_target.read_bytes() == b"must survive"
    finally:
        runtime_lock.release()
        await database.dispose()
        get_settings.cache_clear()


async def test_backup_failure_never_publishes_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "failure-source.db"
    url = await _migrate(source, monkeypatch)
    database = Database(url)
    destination = tmp_path / "failed.db"
    try:
        with (
            patch.object(SQLiteBackupService, "_validate", side_effect=RuntimeError("bad")),
            pytest.raises(RuntimeError, match="bad"),
        ):
            await SQLiteBackupService(database).create(destination)
        assert not destination.exists()
        assert [name for name in os.listdir(tmp_path) if name.endswith(".partial")] == []
        with sqlite3.connect(source) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        await database.dispose()
        get_settings.cache_clear()


async def test_automatic_recovery_writes_one_system_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "automatic-recovery.db"
    url = await _migrate(source, monkeypatch)
    settings = Settings(_env_file=None, database_url=url, temp_dir=tmp_path / "temp")
    os.mkdir(settings.temp_dir)
    database = Database(url)
    try:
        recovery = await _make_recovery_service(database, settings)
        await recovery.recover_startup()
        events = await OperationalAuditService(database).list_recent(limit=10)
        assert len(events) == 1
        assert events[0].event_type is OperationalAuditEventType.CRASH_RECOVERY_COMPLETED
        assert events[0].actor_kind is OperationalAuditActorKind.SYSTEM
    finally:
        await database.dispose()
        get_settings.cache_clear()


async def test_status_and_offline_tools_are_network_free_locked_and_audited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "ops.db"
    url = await _migrate(source, monkeypatch)
    temp_dir = tmp_path / "artifacts"
    temp_dir.mkdir()
    settings = Settings(_env_file=None, database_url=url, temp_dir=temp_dir)

    status = await _status(settings)
    assert status.schema_current
    assert status.actual_workers == "unavailable_outside_running_process"
    assert not status.application_instance_active

    inspection = await _offline_recovery(settings, inspect_only=True)
    assert inspection.expired_download_leases == 0
    summary = await _offline_recovery(settings, inspect_only=False)
    assert summary.download_jobs_recovered == 0
    stale = temp_dir / uuid4().hex
    os.mkdir(stale)
    old = time.time() - settings.temp_artifact_stale_after_seconds - 1
    os.utime(stale, (old, old))
    scan = await _offline_artifacts(settings, scan_only=True)
    assert scan.removed_stale == 0
    assert scan.stale_candidates == 1
    cleanup = await _offline_artifacts(settings, scan_only=False)
    assert cleanup.removed_stale == 1

    database = Database(url)
    try:
        events = await OperationalAuditService(database).list_recent(limit=10)
        assert [event.event_type for event in events] == [
            OperationalAuditEventType.MANUAL_ARTIFACT_CLEANUP_EXECUTED,
            OperationalAuditEventType.MANUAL_RECOVERY_EXECUTED,
        ]
    finally:
        await database.dispose()

    lock = ApplicationInstanceLock.from_database_url(url)
    lock.acquire()
    try:
        before = source.stat().st_mtime_ns
        with pytest.raises(ApplicationInstanceAlreadyRunningError):
            await _offline_recovery(settings, inspect_only=False)
        with pytest.raises(ApplicationInstanceAlreadyRunningError):
            await _offline_artifacts(settings, scan_only=False)
        assert source.stat().st_mtime_ns == before
        live_status = await _status(settings)
        assert live_status.application_instance_active
        destination = tmp_path / "live-backup.db"
        database = Database(url)
        try:
            await SQLiteBackupService(database).create(destination)
        finally:
            await database.dispose()
        assert destination.exists()
    finally:
        lock.release()
        get_settings.cache_clear()


async def test_online_backup_remains_valid_during_controlled_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "concurrent.db"
    url = await _migrate(source, monkeypatch)
    stop = asyncio.Event()
    writes = 0
    with closing(sqlite3.connect(source)) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)

    async def writer() -> None:
        nonlocal writes
        value = 1
        while not stop.is_set():
            try:
                with closing(sqlite3.connect(source, timeout=5)) as connection:
                    connection.execute(
                        "INSERT OR REPLACE INTO tracks (id, title, created_at, updated_at) "
                        "VALUES (1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                        (f"value-{value}",),
                    )
                    connection.commit()
                    writes += 1
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
            value += 1
            await asyncio.sleep(0)

    database = Database(url)
    task = asyncio.create_task(writer())
    try:
        await asyncio.sleep(0.01)
        result = await SQLiteBackupService(database).create(tmp_path / "concurrent-backup.db")
    finally:
        stop.set()
        await task
        await database.dispose()
        get_settings.cache_clear()
    assert writes > 0
    with sqlite3.connect(result.destination) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "20260820_0011",
        )
