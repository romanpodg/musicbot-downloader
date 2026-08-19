from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from app.services.artifacts import ArtifactPathError, DownloadArtifactManager
from app.services.queues import UploadQueueService
from app.storage import Database


def test_unique_jobs_are_owned_and_release_is_idempotent(tmp_path: Path) -> None:
    manager = DownloadArtifactManager(tmp_path)
    first, first_path = manager.create_job()
    second, second_path = manager.create_job()

    assert first != second
    assert first_path.parent == tmp_path.resolve()
    assert second_path.parent == tmp_path.resolve()
    manager.release(first)
    manager.release(first)
    assert not first_path.exists()
    assert second_path.exists()


def test_attempt_cleanup_does_not_remove_other_attempts(tmp_path: Path) -> None:
    manager = DownloadArtifactManager(tmp_path)
    job_id, _ = manager.create_job()
    first = manager.attempt_path(job_id, 1)
    second = manager.attempt_path(job_id, 2)
    (first / "source" / "native.part").write_bytes(b"partial")

    manager.cleanup_attempt(job_id, 1)

    assert not first.exists()
    assert second.exists()


def test_path_escape_and_invalid_release_are_rejected(tmp_path: Path) -> None:
    manager = DownloadArtifactManager(tmp_path)
    job_id, _ = manager.create_job()

    with pytest.raises(ArtifactPathError):
        manager.ensure_owned(tmp_path.parent / "outside", job_id)
    with pytest.raises(ArtifactPathError):
        manager.release("../outside")


def test_final_artifact_symlink_escape_is_never_read_or_followed_on_release(
    tmp_path: Path,
) -> None:
    manager = DownloadArtifactManager(tmp_path / "artifacts")
    job_id, job_path = manager.create_job()
    external = tmp_path / "external.flac"
    external.write_bytes(b"must survive")
    link = job_path / "output" / "final.flac"
    try:
        link.symlink_to(external)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    queue = UploadQueueService(cast(Database, object()), manager)
    with pytest.raises(ArtifactPathError):
        queue.validate_artifact(job_id, f"{job_id}/output/final.flac")

    manager.release(job_id)
    assert external.read_bytes() == b"must survive"
