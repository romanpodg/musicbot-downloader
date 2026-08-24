from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from app.composition import Stage9Components
from app.core.enums import (
    DownloadPlanOperation,
    MusicProviderName,
    QualityProfile,
    QueueErrorCode,
    QueueJobStatus,
    SubscriberStatus,
    TelegramCacheStatus,
    TelegramMediaKind,
)
from app.services.artifact_cleanup import (
    StaleArtifactCleanupManager,
    StaleArtifactCleanupService,
)
from app.services.artifacts import ActiveArtifactRegistry, DownloadArtifactManager
from app.services.crash_recovery import CrashRecoveryService
from app.services.queues import DownloadQueueService, UploadQueueService
from app.services.singleflight import SingleFlightService
from app.storage import Database
from app.storage.models import TelegramFileCache, UploadJob


@dataclass
class ManualClock:
    now: datetime = datetime(2026, 8, 20, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class DatabaseOnlyAlbumCoordinator:
    def __init__(self) -> None:
        self.calls = 0

    async def reconcile(self, *, notify: bool = True) -> int:
        assert notify is False
        self.calls += 1
        return 0


async def _track(database: Database) -> int:
    async with database.transaction() as repositories:
        return (await repositories.tracks.create_track(title="Synthetic", artist="Tests")).id


async def _pending_upload(
    database: Database,
    artifacts: DownloadArtifactManager,
    clock: ManualClock,
    *,
    singleflight: SingleFlightService | None = None,
) -> tuple[int, int, str, Path, str | None]:
    track_id = await _track(database)
    if singleflight is None:
        submitted = await DownloadQueueService(database, max_size=10, clock=clock).submit(
            track_id=track_id, quality_profile=QualityProfile.MP3_320
        )
        download_job_id = submitted.id
        subscriber_id = None
    else:
        submitted_flight = await singleflight.submit(
            track_id=track_id,
            quality_profile=QualityProfile.MP3_320,
            request_key="stage122",
        )
        download_job_id = submitted_flight.download_job.id
        subscriber_id = submitted_flight.subscriber.id
    async with database.transaction() as repositories:
        claimed = await repositories.download_jobs.claim(
            worker_id="download-before-crash",
            now=clock(),
            lease_expires_at=clock() + timedelta(minutes=5),
        )
    assert claimed is not None
    artifact_job_id, _ = artifacts.create_job()
    path = artifacts.final_path(artifact_job_id, "mp3")
    path.write_bytes(b"synthetic audio")
    artifacts.mark_inactive(artifact_job_id)
    stored = path.relative_to(artifacts.root).as_posix()
    async with database.transaction() as repositories:
        upload = await repositories.download_jobs.handoff(
            job_id=download_job_id,
            worker_id="download-before-crash",
            artifact_job_id=artifact_job_id,
            artifact_path=stored,
            now=clock(),
        )
    assert upload is not None
    return upload.id, track_id, artifact_job_id, path, subscriber_id


def _recovery(
    database: Database,
    uploads: UploadQueueService,
    singleflight: SingleFlightService,
    clock: ManualClock,
    album: DatabaseOnlyAlbumCoordinator | None = None,
) -> CrashRecoveryService:
    return CrashRecoveryService(
        database,
        uploads,
        singleflight,
        album or DatabaseOnlyAlbumCoordinator(),  # type: ignore[arg-type]
        telegram_bot_id=100,
        delivery_max_attempts=3,
        clock=clock,
    )


async def test_recovery_preserves_valid_upload_and_only_reclaims_expired_lease(
    database: Database, tmp_path: Path
) -> None:
    clock = ManualClock()
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    uploads = UploadQueueService(database, artifacts, clock=clock)
    singleflight = SingleFlightService(database, max_size=10, upload_queue=uploads, clock=clock)
    upload_id, _, _, path, _ = await _pending_upload(database, artifacts, clock)
    async with database.transaction() as repositories:
        claimed = await repositories.upload_jobs.claim(
            worker_id="upload-before-crash",
            now=clock(),
            lease_expires_at=clock() + timedelta(seconds=10),
        )
    assert claimed is not None

    first = await _recovery(database, uploads, singleflight, clock).recover_startup()
    assert first.upload_jobs_recovered == 0
    assert (await uploads.get_upload_job(upload_id)).status is QueueJobStatus.RUNNING
    assert path.exists()

    clock.now += timedelta(seconds=11)
    second = await _recovery(database, uploads, singleflight, clock).recover_startup()
    recovered = await uploads.get_upload_job(upload_id)
    assert second.upload_jobs_recovered == 1
    assert recovered.status is QueueJobStatus.QUEUED
    assert recovered.attempt_count == 1
    assert path.exists()


async def test_missing_artifact_fails_flight_once_and_preserves_cancelled_subscriber(
    database: Database, tmp_path: Path
) -> None:
    clock = ManualClock()
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    uploads = UploadQueueService(database, artifacts, clock=clock)
    singleflight = SingleFlightService(database, max_size=10, upload_queue=uploads, clock=clock)
    upload_id, track_id, _, path, waiting_id = await _pending_upload(
        database, artifacts, clock, singleflight=singleflight
    )
    assert waiting_id is not None
    second = await singleflight.submit(
        track_id=track_id,
        quality_profile=QualityProfile.MP3_320,
        request_key="cancelled",
    )
    await singleflight.cancel_subscriber(second.subscriber.id)
    path.unlink()
    recovery = _recovery(database, uploads, singleflight, clock)

    first = await recovery.recover_startup()
    second_summary = await recovery.recover_startup()

    failed = await uploads.get_upload_job(upload_id)
    assert failed.status is QueueJobStatus.FAILED
    assert failed.last_error_code == QueueErrorCode.UPLOAD_ARTIFACT_MISSING.value
    assert (await singleflight.get_subscriber(waiting_id)).status is SubscriberStatus.FAILED
    assert (
        await singleflight.get_subscriber(second.subscriber.id)
    ).status is SubscriberStatus.CANCELLED
    assert first.upload_artifacts_failed == 1
    assert second_summary.upload_artifacts_failed == 0


def _cache_row(track_id: int, quality: QualityProfile, *, bot_id: int = 100) -> TelegramFileCache:
    return TelegramFileCache(
        telegram_bot_id=bot_id,
        track_id=track_id,
        quality_profile=quality,
        telegram_file_id="file-id",
        telegram_file_unique_id="unique-id",
        telegram_media_kind=TelegramMediaKind.AUDIO,
        cache_chat_id=-1001,
        cache_message_id=42,
        file_size_bytes=15,
        source_provider=MusicProviderName.QOBUZ,
        source_provider_track_id="source",
        operation=DownloadPlanOperation.DIRECT,
        transcoded=False,
        status=TelegramCacheStatus.ACTIVE,
    )


async def test_exact_active_cache_rescues_upload_without_external_work(
    database: Database, tmp_path: Path
) -> None:
    clock = ManualClock()
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    uploads = UploadQueueService(database, artifacts, clock=clock)
    singleflight = SingleFlightService(database, max_size=10, upload_queue=uploads, clock=clock)
    upload_id, track_id, artifact_id, _, subscriber_id = await _pending_upload(
        database, artifacts, clock, singleflight=singleflight
    )
    async with database.transaction() as repositories:
        repositories.telegram_cache._session.add(_cache_row(track_id, QualityProfile.MP3_320))

    summary = await _recovery(database, uploads, singleflight, clock).recover_startup()

    assert summary.uploads_recovered_from_cache == 1
    assert (await uploads.get_upload_job(upload_id)).status is QueueJobStatus.SUCCEEDED
    assert subscriber_id is not None
    assert (await singleflight.get_subscriber(subscriber_id)).status is SubscriberStatus.READY
    assert not (artifacts.root / artifact_id).exists()


@pytest.mark.parametrize(
    ("quality", "bot_id", "status"),
    [
        (QualityProfile.LOSSLESS, 100, TelegramCacheStatus.ACTIVE),
        (QualityProfile.MP3_320, 101, TelegramCacheStatus.ACTIVE),
        (QualityProfile.MP3_320, 100, TelegramCacheStatus.INVALID),
    ],
)
async def test_inexact_or_invalid_cache_never_rescues(
    database: Database,
    tmp_path: Path,
    quality: QualityProfile,
    bot_id: int,
    status: TelegramCacheStatus,
) -> None:
    clock = ManualClock()
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    uploads = UploadQueueService(database, artifacts, clock=clock)
    singleflight = SingleFlightService(database, max_size=10, upload_queue=uploads, clock=clock)
    upload_id, track_id, _, path, _ = await _pending_upload(database, artifacts, clock)
    row = _cache_row(track_id, quality, bot_id=bot_id)
    if status is TelegramCacheStatus.INVALID:
        row.status = status
        row.invalidated_at = clock()
    async with database.transaction() as repositories:
        repositories.telegram_cache._session.add(row)

    summary = await _recovery(database, uploads, singleflight, clock).recover_startup()

    assert summary.uploads_recovered_from_cache == 0
    assert (await uploads.get_upload_job(upload_id)).status is QueueJobStatus.QUEUED
    assert path.exists()


async def test_cleanup_classifies_owned_active_young_stale_and_unknown(
    database: Database, tmp_path: Path
) -> None:
    clock = ManualClock()
    root = tmp_path / "temp"
    registry = ActiveArtifactRegistry()
    artifacts = DownloadArtifactManager(root, registry)
    await _pending_upload(database, artifacts, clock)
    owned = next(root.iterdir())
    active_id, active = artifacts.create_job()
    young_id = "1" * 32
    young = root / young_id
    young.mkdir()
    stale_id = "2" * 32
    stale = root / stale_id
    stale.mkdir()
    unknown = root / "do-not-delete"
    unknown.mkdir()
    old = clock().timestamp() - 7200
    os.utime(owned, (old, old))
    os.utime(active, (old, old))
    os.utime(stale, (old, old))
    os.utime(young, (clock().timestamp(), clock().timestamp()))
    service = StaleArtifactCleanupService(
        database, root, registry, stale_after_seconds=3600, clock=lambda: clock().timestamp()
    )

    summary = await service.sweep()

    assert summary.preserved_owned == 1
    assert summary.preserved_active == 1
    assert summary.preserved_young == 1
    assert summary.removed_stale == 1
    assert summary.unknown == 1
    assert owned.exists() and active.exists() and young.exists() and unknown.exists()
    assert not stale.exists()
    artifacts.mark_inactive(active_id)


@pytest.mark.parametrize("owner_state", ["queued", "running", "retry_delayed"])
async def test_cleanup_preserves_every_nonterminal_upload_owner(
    database: Database, tmp_path: Path, owner_state: str
) -> None:
    clock = ManualClock()
    root = tmp_path / "temp"
    artifacts = DownloadArtifactManager(root)
    upload_id, _, artifact_id, _, _ = await _pending_upload(database, artifacts, clock)
    values: dict[str, object] = {}
    if owner_state == "running":
        values = {
            "status": QueueJobStatus.RUNNING,
            "lease_owner": "upload-before-crash",
            "lease_expires_at": clock() + timedelta(minutes=5),
        }
    elif owner_state == "retry_delayed":
        values = {"available_at": clock() + timedelta(hours=1)}
    if values:
        async with database.engine.begin() as connection:
            await connection.execute(
                update(UploadJob).where(UploadJob.id == upload_id).values(**values)
            )
    artifact_root = root / artifact_id
    old = clock().timestamp() - 7200
    os.utime(artifact_root, (old, old))
    service = StaleArtifactCleanupService(
        database,
        root,
        artifacts.registry,
        stale_after_seconds=3600,
        clock=lambda: clock().timestamp(),
    )

    summary = await service.sweep()

    assert summary.preserved_owned == 1
    assert artifact_root.exists()


async def test_cleanup_removes_old_terminal_upload_leftover(
    database: Database, tmp_path: Path
) -> None:
    clock = ManualClock()
    root = tmp_path / "temp"
    artifacts = DownloadArtifactManager(root)
    upload_id, _, artifact_id, _, _ = await _pending_upload(database, artifacts, clock)
    async with database.engine.begin() as connection:
        await connection.execute(
            update(UploadJob)
            .where(UploadJob.id == upload_id)
            .values(status=QueueJobStatus.SUCCEEDED, finished_at=clock())
        )
    artifact_root = root / artifact_id
    old = clock().timestamp() - 7200
    os.utime(artifact_root, (old, old))
    service = StaleArtifactCleanupService(
        database,
        root,
        artifacts.registry,
        stale_after_seconds=3600,
        clock=lambda: clock().timestamp(),
    )

    summary = await service.sweep()

    assert summary.removed_stale == 1
    assert not artifact_root.exists()


async def test_cleanup_isolates_candidate_failure_and_continues(
    database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "temp"
    root.mkdir()
    denied = root / ("5" * 32)
    removable = root / ("6" * 32)
    denied.mkdir()
    removable.mkdir()
    now = datetime(2026, 8, 20, tzinfo=UTC).timestamp()
    old = now - 7200
    os.utime(denied, (old, old))
    os.utime(removable, (old, old))
    real_rmtree = shutil.rmtree

    def selective_rmtree(path: Path) -> None:
        if path.name == denied.name:
            raise PermissionError("controlled")
        real_rmtree(path)

    monkeypatch.setattr("app.services.artifact_cleanup.shutil.rmtree", selective_rmtree)
    service = StaleArtifactCleanupService(
        database,
        root,
        ActiveArtifactRegistry(),
        stale_after_seconds=3600,
        clock=lambda: now,
    )

    summary = await service.sweep()

    assert summary.errors == 1
    assert summary.removed_stale == 1
    assert denied.exists()
    assert not removable.exists()


async def test_cleanup_never_follows_top_or_inner_symlinks(
    database: Database, tmp_path: Path
) -> None:
    root = tmp_path / "temp"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    top = root / ("3" * 32)
    inner_root = root / ("4" * 32)
    inner_root.mkdir()
    try:
        top.symlink_to(outside)
        (inner_root / "outside-link").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")
    old = datetime(2026, 8, 20, tzinfo=UTC).timestamp() - 7200
    os.utime(inner_root, (old, old))
    service = StaleArtifactCleanupService(
        database,
        root,
        ActiveArtifactRegistry(),
        stale_after_seconds=3600,
        clock=lambda: datetime(2026, 8, 20, tzinfo=UTC).timestamp(),
    )

    first = await service.sweep()
    second = await service.sweep()

    assert first.removed_stale == 1
    assert second.removed_stale == 0
    assert top.is_symlink()
    assert outside.read_text(encoding="utf-8") == "private"


async def test_watchdog_runs_periodically_and_stops_without_leaking() -> None:
    calls = 0
    repeated = asyncio.Event()

    async def sweep() -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            repeated.set()

    manager = StaleArtifactCleanupManager(
        SimpleNamespace(sweep=sweep),
        interval_seconds=0.01,  # type: ignore[arg-type]
    )
    await manager.start()
    await asyncio.wait_for(repeated.wait(), timeout=1.0)
    await manager.stop()
    assert calls >= 2


async def test_component_startup_order_is_recovery_cleanup_then_workers() -> None:
    order: list[str] = []

    def operation(name: str):
        async def run() -> None:
            order.append(name)

        return run

    fake = SimpleNamespace(
        crash_recovery=SimpleNamespace(recover_startup=operation("recovery")),
        artifact_cleanup=SimpleNamespace(sweep=operation("startup-cleanup")),
        provider_accounts=SimpleNamespace(reconcile_startup=operation("provider-reconciliation")),
        queue_manager=SimpleNamespace(start=operation("queue")),
        delivery_fanout=SimpleNamespace(start=operation("delivery")),
        album_coordinator=SimpleNamespace(start=operation("album")),
        cleanup_manager=SimpleNamespace(start=operation("watchdog")),
    )
    await Stage9Components.start(fake)  # type: ignore[arg-type]
    assert order == [
        "recovery",
        "startup-cleanup",
        "provider-reconciliation",
        "queue",
        "delivery",
        "album",
        "watchdog",
    ]


async def test_tampered_recovery_path_never_touches_external_target(
    database: Database, tmp_path: Path
) -> None:
    clock = ManualClock()
    artifacts = DownloadArtifactManager(tmp_path / "temp")
    uploads = UploadQueueService(database, artifacts, clock=clock)
    singleflight = SingleFlightService(database, max_size=10, upload_queue=uploads, clock=clock)
    upload_id, _, _, _, _ = await _pending_upload(database, artifacts, clock)
    external = tmp_path / "external.mp3"
    external.write_bytes(b"private")
    async with database.engine.begin() as connection:
        await connection.execute(
            update(UploadJob).where(UploadJob.id == upload_id).values(artifact_path=str(external))
        )

    summary = await _recovery(database, uploads, singleflight, clock).recover_startup()

    assert summary.upload_artifacts_failed == 1
    assert (await uploads.get_upload_job(upload_id)).last_error_code == (
        QueueErrorCode.UPLOAD_ARTIFACT_INVALID.value
    )
    assert external.read_bytes() == b"private"
