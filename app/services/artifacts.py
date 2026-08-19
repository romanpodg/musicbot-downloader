"""Controlled temporary artifact ownership for one-shot Stage 6 jobs."""

from __future__ import annotations

import re
import shutil
import threading
import uuid
from pathlib import Path

_JOB_ID = re.compile(r"^[0-9a-f]{32}$")


class ArtifactPathError(ValueError):
    pass


class ActiveArtifactRegistry:
    """Process-local protection for artifact roots currently being produced/used."""

    def __init__(self) -> None:
        self._active: set[str] = set()
        self._lock = threading.Lock()

    def mark_active(self, job_id: str) -> None:
        _validate_job_id(job_id)
        with self._lock:
            self._active.add(job_id)

    def mark_inactive(self, job_id: str) -> None:
        with self._lock:
            self._active.discard(job_id)

    def is_active(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._active


class DownloadArtifactManager:
    def __init__(self, temp_dir: Path, registry: ActiveArtifactRegistry | None = None) -> None:
        self._root = temp_dir.expanduser().resolve()
        self._registry = registry or ActiveArtifactRegistry()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def registry(self) -> ActiveArtifactRegistry:
        return self._registry

    def create_job(self) -> tuple[str, Path]:
        self._root.mkdir(parents=True, exist_ok=True)
        job_id = uuid.uuid4().hex
        # Register before the first filesystem write so a concurrent sweep can
        # never observe an unprotected newly-created root.
        self._registry.mark_active(job_id)
        try:
            job_path = self._job_path(job_id)
            job_path.mkdir(mode=0o700)
            (job_path / "output").mkdir(mode=0o700)
        except BaseException:
            self._registry.mark_inactive(job_id)
            raise
        return job_id, job_path

    def mark_inactive(self, job_id: str) -> None:
        self._registry.mark_inactive(job_id)

    def attempt_path(self, job_id: str, plan_rank: int) -> Path:
        if plan_rank < 1:
            raise ArtifactPathError()
        path = self._job_path(job_id) / f"attempt-{plan_rank:03d}"
        self.ensure_owned(path, job_id)
        path.mkdir(mode=0o700, parents=False, exist_ok=False)
        (path / "source").mkdir(mode=0o700)
        return path

    def final_path(self, job_id: str, extension: str) -> Path:
        if not re.fullmatch(r"[a-z0-9]{1,8}", extension):
            raise ArtifactPathError()
        path = self._job_path(job_id) / "output" / f"final.{extension}"
        self.ensure_owned(path, job_id)
        return path

    def cleanup_attempt(self, job_id: str, plan_rank: int) -> None:
        path = self._job_path(job_id) / f"attempt-{plan_rank:03d}"
        self.ensure_owned(path, job_id)
        if path.is_symlink():
            path.unlink(missing_ok=True)
        elif path.exists():
            shutil.rmtree(path)

    def release(self, job_id: str) -> None:
        try:
            path = self._job_path(job_id)
            if path.is_symlink():
                path.unlink(missing_ok=True)
            elif path.exists():
                shutil.rmtree(path)
        finally:
            self._registry.mark_inactive(job_id)

    def ensure_owned(self, path: Path, job_id: str) -> Path:
        job_path = self._job_path(job_id)
        resolved = path.resolve()
        if resolved != job_path and job_path not in resolved.parents:
            raise ArtifactPathError()
        return resolved

    def _job_path(self, job_id: str) -> Path:
        _validate_job_id(job_id)
        path = (self._root / job_id).resolve()
        if path.parent != self._root:
            raise ArtifactPathError()
        return path


def is_artifact_job_id(value: str) -> bool:
    return _JOB_ID.fullmatch(value) is not None


def _validate_job_id(job_id: str) -> None:
    if not _JOB_ID.fullmatch(job_id):
        raise ArtifactPathError()
