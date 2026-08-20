"""Single-host OS advisory lock for one active SQLite application runtime."""

from __future__ import annotations

import errno
import importlib
import os
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO

from sqlalchemy import make_url


class ApplicationInstanceAlreadyRunningError(RuntimeError):
    pass


def sqlite_database_path(database_url: str) -> Path:
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "sqlite" or not parsed.database:
        raise ValueError("operation requires a file-backed SQLite database")
    if parsed.database == ":memory:":
        raise ValueError("operation requires a file-backed SQLite database")
    return Path(parsed.database).expanduser().resolve()


def instance_lock_path(database_url: str) -> Path:
    database_path = sqlite_database_path(database_url)
    return database_path.with_name(f"{database_path.name}.instance.lock")


class ApplicationInstanceLock:
    """Non-blocking lifetime lock; file contents are diagnostics, never authority."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self._file: BinaryIO | None = None

    @classmethod
    def from_database_url(cls, database_url: str) -> ApplicationInstanceLock:
        return cls(instance_lock_path(database_url))

    @property
    def acquired(self) -> bool:
        return self._file is not None

    def acquire(self, *, write_metadata: bool = True) -> None:
        if self._file is not None:
            raise RuntimeError("application instance lock is already acquired by this object")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT
        if os.name != "nt" and hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.path, flags, 0o600)
        handle = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            os.chmod(self.path, 0o600)
            self._lock(handle)
            if write_metadata:
                handle.seek(0)
                handle.truncate()
                metadata = (
                    f"pid={os.getpid()}\nstarted_at={datetime.now(UTC).isoformat()}\n"
                ).encode("ascii")
                handle.write(metadata)
                handle.flush()
            self._file = handle
        except BaseException:
            handle.close()
            raise

    def release(self) -> None:
        handle = self._file
        if handle is None:
            return
        self._file = None
        try:
            self._unlock(handle)
        finally:
            handle.close()

    def probe_active(self) -> bool:
        if self._file is not None:
            return True
        try:
            self.acquire(write_metadata=False)
        except ApplicationInstanceAlreadyRunningError:
            return True
        else:
            self.release()
            return False

    def __enter__(self) -> ApplicationInstanceLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()

    @staticmethod
    def _lock(handle: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            typed = handle
            if os.fstat(typed.fileno()).st_size == 0:
                typed.seek(0)
                typed.write(b"\0")
                typed.flush()
            typed.seek(0)
            try:
                msvcrt.locking(typed.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK, 13, 36}:
                    raise ApplicationInstanceAlreadyRunningError(
                        "another application instance is already running"
                    ) from exc
                raise
            return
        fcntl: Any = importlib.import_module("fcntl")

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ApplicationInstanceAlreadyRunningError(
                "another application instance is already running"
            ) from exc

    @staticmethod
    def _unlock(handle: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
        fcntl: Any = importlib.import_module("fcntl")

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
