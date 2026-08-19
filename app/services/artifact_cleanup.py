"""Conservative cleanup of stale, unowned Stage 6 artifact roots."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.services.artifacts import ActiveArtifactRegistry, is_artifact_job_id
from app.storage import Database

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ArtifactCleanupSummary:
    scanned: int = 0
    preserved_active: int = 0
    preserved_owned: int = 0
    preserved_young: int = 0
    removed_stale: int = 0
    unknown: int = 0
    errors: int = 0


class StaleArtifactCleanupService:
    def __init__(
        self,
        database: Database,
        temp_dir: Path,
        registry: ActiveArtifactRegistry,
        *,
        stale_after_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._database = database
        self._root = temp_dir.expanduser().resolve()
        self._registry = registry
        self._stale_after = stale_after_seconds
        self._clock = clock

    async def sweep(self) -> ArtifactCleanupSummary:
        logger.info("artifact_cleanup_started")
        try:
            async with self._database.transaction() as repositories:
                protected = await repositories.upload_jobs.protected_artifact_job_ids()
            summary = await asyncio.to_thread(self._sweep_sync, protected, self._clock())
        except Exception:
            logger.exception("artifact_cleanup_failed")
            raise
        logger.info(
            "artifact_cleanup_completed",
            extra={
                "scanned": summary.scanned,
                "preserved_active": summary.preserved_active,
                "preserved_owned": summary.preserved_owned,
                "preserved_young": summary.preserved_young,
                "removed_stale": summary.removed_stale,
                "unknown": summary.unknown,
                "errors": summary.errors,
            },
        )
        return summary

    def _sweep_sync(self, protected: set[str], now: float) -> ArtifactCleanupSummary:
        counts = {
            "scanned": 0,
            "preserved_active": 0,
            "preserved_owned": 0,
            "preserved_young": 0,
            "removed_stale": 0,
            "unknown": 0,
            "errors": 0,
        }
        try:
            entries = os.scandir(self._root)
        except OSError:
            logger.exception("artifact_cleanup_failed", extra={"scope": "temp_root"})
            counts["errors"] += 1
            return ArtifactCleanupSummary(**counts)

        with entries:
            for entry in entries:
                counts["scanned"] += 1
                job_id = entry.name
                if not is_artifact_job_id(job_id):
                    counts["unknown"] += 1
                    continue
                try:
                    metadata = entry.stat(follow_symlinks=False)
                    # A UUID-named symlink/reparse point is not enough ownership
                    # proof. Ignore it and never inspect or remove its target.
                    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                        counts["unknown"] += 1
                        continue
                    if self._registry.is_active(job_id):
                        counts["preserved_active"] += 1
                        continue
                    if job_id in protected:
                        counts["preserved_owned"] += 1
                        continue
                    if now - metadata.st_mtime < self._stale_after:
                        counts["preserved_young"] += 1
                        continue
                    candidate = Path(entry.path)
                    # Resolve only the controlled top-level directory. Inner
                    # symlinks are unlinked by shutil.rmtree, never traversed.
                    if (
                        candidate.parent.resolve() != self._root
                        or candidate.resolve().parent != self._root
                    ):
                        counts["unknown"] += 1
                        continue
                    current = candidate.lstat()
                    if stat.S_ISLNK(current.st_mode) or not stat.S_ISDIR(current.st_mode):
                        counts["unknown"] += 1
                        continue
                    shutil.rmtree(candidate)
                    counts["removed_stale"] += 1
                except FileNotFoundError:
                    # A worker may release the artifact between classification
                    # and deletion; that is a successful idempotent outcome.
                    continue
                except (PermissionError, OSError):
                    counts["errors"] += 1
                    logger.warning(
                        "artifact_cleanup_candidate_failed",
                        extra={"artifact_job_id": job_id},
                    )
        return ArtifactCleanupSummary(**counts)


class StaleArtifactCleanupManager:
    """Owned periodic task; unexpected termination is observable by supervision."""

    def __init__(self, service: StaleArtifactCleanupService, *, interval_seconds: float) -> None:
        self._service = service
        self._interval = interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="artifact-cleanup-watchdog")

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(task, timeout=30.0)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        finally:
            self._task = None

    async def wait_terminated(self) -> None:
        task = self._task
        if task is None:
            raise RuntimeError("artifact cleanup watchdog is not running")
        await task
        if not self._stop.is_set():
            raise RuntimeError("artifact cleanup watchdog stopped unexpectedly")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                await self._service.sweep()
