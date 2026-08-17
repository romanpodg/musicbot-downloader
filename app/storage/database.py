"""Async database lifecycle and isolated SQLite configuration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import event, make_url
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.exceptions import DatabaseConcurrencyError, DatabaseError
from app.storage.repositories import (
    DownloadJobRepository,
    RuntimeSettingsRepository,
    SingleFlightRepository,
    TrackRepository,
    TrackSourceRepository,
    UploadJobRepository,
    UserRepository,
)


@dataclass(frozen=True, slots=True)
class Repositories:
    users: UserRepository
    tracks: TrackRepository
    track_sources: TrackSourceRepository
    download_jobs: DownloadJobRepository
    upload_jobs: UploadJobRepository
    runtime_settings: RuntimeSettingsRepository
    singleflight: SingleFlightRepository


class Database:
    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = url
        self._ensure_sqlite_directory(url)
        self.engine = create_async_engine(url, echo=echo)
        if self.engine.url.get_backend_name() == "sqlite":
            event.listen(self.engine.sync_engine, "connect", self._configure_sqlite)
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Repositories]:
        async with self._sessions() as session:
            repositories = self._repositories(session)
            try:
                yield repositories
                await session.commit()
            except SQLAlchemyError as exc:
                await session.rollback()
                if self._is_concurrency_error(exc):
                    raise DatabaseConcurrencyError() from exc
                raise DatabaseError() from exc
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self.engine.dispose()

    @staticmethod
    def _repositories(session: AsyncSession) -> Repositories:
        return Repositories(
            users=UserRepository(session),
            tracks=TrackRepository(session),
            track_sources=TrackSourceRepository(session),
            download_jobs=DownloadJobRepository(session),
            upload_jobs=UploadJobRepository(session),
            runtime_settings=RuntimeSettingsRepository(session),
            singleflight=SingleFlightRepository(session),
        )

    @staticmethod
    def _configure_sqlite(dbapi_connection: Any, _: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()

    @staticmethod
    def _ensure_sqlite_directory(url: str) -> None:
        parsed = make_url(url)
        if parsed.get_backend_name() != "sqlite" or not parsed.database:
            return
        if parsed.database == ":memory:":
            return
        Path(parsed.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    def _is_concurrency_error(self, exc: SQLAlchemyError) -> bool:
        if isinstance(exc, IntegrityError):
            if self.engine.url.get_backend_name() != "sqlite":
                return False
            message = str(exc).lower()
            return (
                "unique constraint failed: track_sources.provider, track_sources.provider_track_id"
            ) in message
        if not isinstance(exc, OperationalError):
            return False
        if self.engine.url.get_backend_name() != "sqlite":
            return False
        message = str(exc).lower()
        return "database is locked" in message or "database table is locked" in message


async def scalar_pragma(engine: AsyncEngine, pragma: str) -> Any:
    """Read an allow-listed SQLite PRAGMA for diagnostics and tests."""

    if pragma not in {"journal_mode", "foreign_keys", "busy_timeout"}:
        raise ValueError("Unsupported PRAGMA")
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync_connection: _read_pragma(sync_connection, pragma)
        )


def _read_pragma(connection: Connection, pragma: str) -> Any:
    return connection.exec_driver_sql(f"PRAGMA {pragma}").scalar_one()
