"""Local-only production preflight and temporary-disk protection."""

from __future__ import annotations

import errno
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RuntimePrerequisiteReport:
    temp_dir: Path
    ffmpeg_available: bool
    ffprobe_available: bool


class DiskUsage(Protocol):
    @property
    def free(self) -> int: ...


def _disk_usage(path: Path) -> DiskUsage:
    return shutil.disk_usage(path)


class TemporaryDiskGuard:
    """Enforce a free-space floor before starting a fresh media acquisition."""

    def __init__(
        self,
        temp_dir: Path,
        minimum_free_bytes: int,
        *,
        disk_usage: Callable[[Path], DiskUsage] = _disk_usage,
        maximum_usage_bytes: int | None = None,
    ) -> None:
        self._temp_dir = temp_dir.expanduser().resolve()
        self._minimum_free_bytes = minimum_free_bytes
        self._disk_usage = disk_usage
        self._maximum_usage_bytes = maximum_usage_bytes

    @property
    def minimum_free_bytes(self) -> int:
        return self._minimum_free_bytes

    def ensure_available(self) -> None:
        usage = self._disk_usage(self._temp_dir)
        free = usage.free
        if free < self._minimum_free_bytes:
            raise OSError(errno.ENOSPC, "temporary storage reserve is exhausted")
        if self._maximum_usage_bytes is not None:
            used = _directory_size(self._temp_dir)
            if used >= self._maximum_usage_bytes:
                raise OSError(errno.ENOSPC, "temporary storage usage limit is exhausted")

    def snapshot(self) -> tuple[int, int, bool]:
        """Return used bytes, free bytes, and whether new work is blocked."""
        free = self._disk_usage(self._temp_dir).free
        used = _directory_size(self._temp_dir)
        blocked = free < self._minimum_free_bytes or (
            self._maximum_usage_bytes is not None and used >= self._maximum_usage_bytes
        )
        return used, free, blocked


def _directory_size(root: Path) -> int:
    total = 0
    if not root.exists():
        return 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total


class RuntimePrerequisiteService:
    """Validate fatal local prerequisites without network access or socket binding."""

    def __init__(
        self,
        temp_dir: Path,
        *,
        ffmpeg_binary: str | None = None,
        ffprobe_binary: str | None = None,
    ) -> None:
        self._temp_dir = temp_dir.expanduser().resolve()
        self._ffmpeg_binary = ffmpeg_binary or "ffmpeg"
        self._ffprobe_binary = ffprobe_binary or "ffprobe"

    def check(self) -> RuntimePrerequisiteReport:
        self._verify_temp_dir_writable()
        return RuntimePrerequisiteReport(
            temp_dir=self._temp_dir,
            ffmpeg_available=_executable_available(self._ffmpeg_binary),
            ffprobe_available=_executable_available(self._ffprobe_binary),
        )

    def _verify_temp_dir_writable(self) -> None:
        try:
            self._temp_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
            if not self._temp_dir.is_dir():
                raise NotADirectoryError(self._temp_dir)
            probe_dir = Path(tempfile.mkdtemp(prefix=".musicbot-write-probe-", dir=self._temp_dir))
            probe_file = probe_dir / "probe"
            descriptor = os.open(probe_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            probe_file.unlink()
            probe_dir.rmdir()
        except OSError as exc:
            raise OSError(errno.EACCES, "TEMP_DIR is not writable") from exc


def _executable_available(binary: str) -> bool:
    return shutil.which(binary) is not None or Path(binary).is_file()
