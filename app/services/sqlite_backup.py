"""Validated SQLite online backup with an atomic, non-overwriting destination."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from app.services.instance_lock import sqlite_database_path
from app.services.operational_audit import OperationalAuditService
from app.storage import Database


@dataclass(frozen=True, slots=True)
class SQLiteBackupResult:
    destination: Path
    size_bytes: int
    schema_revision: str


class SQLiteBackupService:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._audit = OperationalAuditService(database)

    async def create(self, destination: Path) -> SQLiteBackupResult:
        source = sqlite_database_path(self._database.url)
        result = await asyncio.to_thread(self._create_sync, source, destination)
        # This event intentionally follows the snapshot and is not in that backup file.
        await self._audit.append_backup(
            destination=result.destination,
            size_bytes=result.size_bytes,
            schema_revision=result.schema_revision,
        )
        return result

    @staticmethod
    def _create_sync(source: Path, destination: Path) -> SQLiteBackupResult:
        source = source.resolve(strict=True)
        requested = destination.expanduser()
        if not requested.name:
            raise ValueError("backup destination must be a file path")
        parent = requested.parent.resolve(strict=True)
        final = parent / requested.name
        if final.resolve() == source:
            raise ValueError("backup destination must differ from the source database")
        if os.path.lexists(final):
            raise FileExistsError("backup destination already exists")

        descriptor, partial_name = tempfile.mkstemp(
            prefix=f".{final.name}.", suffix=".partial", dir=parent
        )
        os.close(descriptor)
        partial = Path(partial_name)
        try:
            os.chmod(partial, 0o600)
            source_uri = f"file:{quote(source.as_posix(), safe='/:')}?mode=ro"
            with (
                closing(sqlite3.connect(source_uri, uri=True, timeout=30.0)) as source_connection,
                closing(sqlite3.connect(partial, timeout=30.0)) as backup_connection,
            ):
                source_connection.backup(backup_connection, pages=256, sleep=0.01)
                backup_connection.commit()
            revision = SQLiteBackupService._validate(partial)
            os.chmod(partial, 0o600)
            # Windows rename refuses an existing destination; POSIX hard-link creation
            # provides the same atomic no-overwrite publication contract.
            if os.name == "nt":
                os.rename(partial, final)
            else:
                os.link(partial, final)
                partial.unlink()
            os.chmod(final, 0o600)
            return SQLiteBackupResult(
                destination=final,
                size_bytes=final.stat().st_size,
                schema_revision=revision,
            )
        except BaseException:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    @staticmethod
    def _validate(path: Path) -> str:
        uri = f"file:{quote(path.resolve().as_posix(), safe='/:')}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=30.0)) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise RuntimeError("backup integrity validation failed")
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            if row is None or not isinstance(row[0], str) or not row[0]:
                raise RuntimeError("backup schema revision is unavailable")
            return row[0]
