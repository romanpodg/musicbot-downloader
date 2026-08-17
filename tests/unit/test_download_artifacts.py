from __future__ import annotations

from pathlib import Path

import pytest

from app.services.artifacts import ArtifactPathError, DownloadArtifactManager


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
