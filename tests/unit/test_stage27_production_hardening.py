from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.core.download_preferences import UserDownloadPreferences
from app.core.enums import BatchSourceType, BatchStatus, MusicProviderName, UserRole
from app.core.exceptions import AlbumTooLarge, DownloadPipelineError
from app.core.models import (
    AlbumSnapshot,
    AlbumTrackSnapshot,
    QueueRuntimeSnapshot,
    QueueStatusCounts,
    ResolvedCollection,
    ResolvedCollectionItem,
    WorkerPoolSnapshot,
)
from app.services.artifact_cleanup import StaleArtifactCleanupService
from app.services.artifacts import ActiveArtifactRegistry, DownloadArtifactManager
from app.services.authorization import AuthorizationError, TelegramAuthorizationService
from app.services.batch_download import BatchDownloadService, BatchLimitExceeded
from app.services.download_history import DownloadHistoryService
from app.services.provider_limits import ProviderRateLimiter
from app.services.runtime_prerequisites import TemporaryDiskGuard
from app.services.system_diagnostics import SystemDiagnosticsService
from app.services.telegram_albums import TelegramAlbumRequestService
from app.services.workers import DownloadWorkerBackend
from app.storage.models.base import utc_now


@pytest.mark.asyncio
async def test_provider_limiter_is_scoped_and_cancellation_safe() -> None:
    limiter = ProviderRateLimiter(interval_seconds=0, max_concurrent=1)
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold() -> None:
        async with limiter.operation(MusicProviderName.TIDAL):
            started.set()
            await release.wait()

    first = asyncio.create_task(hold())
    await started.wait()

    async def wait_for_tidal() -> None:
        async with limiter.operation(MusicProviderName.TIDAL):
            pass

    blocked = asyncio.create_task(wait_for_tidal())
    await asyncio.sleep(0)
    assert not blocked.done()
    async with limiter.operation(MusicProviderName.DEEZER):
        assert limiter.active[MusicProviderName.DEEZER] == 1
    blocked.cancel()
    await asyncio.gather(blocked, return_exceptions=True)
    release.set()
    await first
    assert limiter.active[MusicProviderName.TIDAL] == 0


@pytest.mark.asyncio
async def test_provider_health_and_local_throttle_remain_distinct(database, tmp_path: Path) -> None:
    limiter = ProviderRateLimiter(interval_seconds=0, max_concurrent=1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold() -> None:
        async with limiter.operation(MusicProviderName.TIDAL):
            entered.set()
            await release.wait()

    first = asyncio.create_task(hold())
    await entered.wait()
    second = asyncio.create_task(_limited(limiter, MusicProviderName.TIDAL))
    await asyncio.sleep(0)

    class Health:
        async def check_all(self, actor_user_id: int):
            del actor_user_id
            entry = SimpleNamespace(
                provider=MusicProviderName.TIDAL, status=SimpleNamespace(value="READY")
            )
            return SimpleNamespace(entries=(entry,))

    diagnostics = SystemDiagnosticsService(
        database,
        _QueueSnapshot(),
        temp_dir=tmp_path,
        temp_reserve_bytes=0,
        temp_max_bytes=10_000_000,
        provider_health=Health(),  # type: ignore[arg-type]
        provider_limiter=limiter,
    )
    result = await diagnostics.system(1)
    tidal = next(item for item in result.providers if item.provider is MusicProviderName.TIDAL)
    assert (tidal.health, tidal.active_operations, tidal.throttle) == ("ready", 1, "waiting")
    assert "provider local throttling" in result.reasons
    release.set()
    await asyncio.gather(first, second)


@pytest.mark.asyncio
async def test_system_and_job_authorization_and_bounded_redaction(database, tmp_path: Path) -> None:
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(27005)
        admin = await repositories.users.create_user(27006)
        owner = await repositories.users.create_user(27007)
        await repositories.users.set_role(admin, UserRole.ADMIN)
        await repositories.users.set_role(owner, UserRole.OWNER)
    service = SystemDiagnosticsService(
        database,
        _QueueSnapshot(),
        temp_dir=tmp_path,
        temp_reserve_bytes=0,
        temp_max_bytes=10_000_000,
        authorization=TelegramAuthorizationService(database, owner_id=owner.telegram_id),
    )
    with pytest.raises(AuthorizationError):
        await service.system(user.id)
    with pytest.raises(AuthorizationError):
        await service.job(1, user.id)
    assert (await service.system(admin.id)).providers == ()
    assert await service.job(1, admin.id) is None
    assert (await service.system(owner.id)).storage.pressure == "normal"
    # `/job` exposes normalized codes only; persisted provider detail is never
    # part of the diagnostic DTO or renderer.
    assert "last_error_detail" not in SystemDiagnosticsService.job.__code__.co_names


class _Usage:
    def __init__(self, free: int) -> None:
        self.free = free


@pytest.mark.parametrize(
    ("free", "file_size", "allowed"),
    ((100, 9, True), (99, 9, False), (100, 11, False)),
)
def test_storage_guard_enforces_reserve_and_usage(
    tmp_path: Path, free: int, file_size: int, allowed: bool
) -> None:
    (tmp_path / "artifact").write_bytes(b"x" * file_size)
    guard = TemporaryDiskGuard(
        tmp_path,
        100,
        maximum_usage_bytes=10,
        disk_usage=lambda _: _Usage(free),
    )
    if allowed:
        guard.ensure_available()
    else:
        with pytest.raises(OSError):
            guard.ensure_available()


def test_stage27_settings_are_finite_and_cross_validated() -> None:
    settings = Settings()
    assert settings.global_active_download_limit == settings.download_workers_max
    assert settings.global_active_upload_limit == settings.upload_workers_max
    assert settings.max_collection_size == settings.max_batch_items
    with pytest.raises(ValidationError, match="greater than or equal"):
        Settings(temp_dir_max_bytes=0)


@pytest.mark.asyncio
async def test_playlist_cap_rejects_before_snapshot_or_children(database) -> None:
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(27001)
    service = BatchDownloadService(
        database, _CollectionResolver(_collection("one", "two")), max_items=1
    )
    with pytest.raises(BatchLimitExceeded):
        await service.expand(
            user_id=user.id,
            confirmation_id="playlist-over-limit",
            source_type=BatchSourceType.PLAYLIST,
            source_reference="playlist",
            preferences=UserDownloadPreferences(user.id),
        )
    async with database.transaction() as repositories:
        assert await repositories.batch_download.get_by_confirmation("playlist-over-limit") is None


@pytest.mark.asyncio
async def test_album_cap_rejects_legacy_snapshot_before_any_children(database) -> None:
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(27002)
    service = TelegramAlbumRequestService(
        database,
        _AlbumResolver(_album("one", "two")),  # type: ignore[arg-type]
        telegram_bot_id=1,
        max_items=1,
    )
    with pytest.raises(AlbumTooLarge):
        await service.request_album(
            user=user,
            telegram_chat_id=user.telegram_id,
            source_message_id=1,
            url="https://example.invalid/album",
        )
    async with database.transaction() as repositories:
        assert (
            await repositories.telegram_album.get_by_message(
                telegram_bot_id=1,
                telegram_chat_id=user.telegram_id,
                source_message_id=1,
            )
            is None
        )


@pytest.mark.asyncio
async def test_history_repeat_rechecks_current_collection_cap_without_partial_batch(
    database,
) -> None:
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(27003)
    initial = BatchDownloadService(
        database, _CollectionResolver(_collection("one", "two")), max_items=2
    )
    original = await initial.expand(
        user_id=user.id,
        confirmation_id="original-complete",
        source_type=BatchSourceType.PLAYLIST,
        source_reference="playlist",
        preferences=UserDownloadPreferences(user.id),
    )
    async with database.transaction() as repositories:
        await repositories.batch_download.mark_status(original.id, BatchStatus.COMPLETED, utc_now())
    history = DownloadHistoryService(
        database,
        batch_download=BatchDownloadService(
            database, _CollectionResolver(_collection("one")), max_items=1
        ),
    )
    target = SimpleNamespace(user_id=user.id, source_message_id=99)
    with pytest.raises(BatchLimitExceeded):
        await history.repeat_batch(user.id, original.id, target=target)  # type: ignore[arg-type]
    async with database.transaction() as repositories:
        assert (
            await repositories.batch_download.get_by_confirmation(f"repeat-batch:{original.id}:99")
            is None
        )


@pytest.mark.asyncio
async def test_retry_uses_failed_subset_even_when_original_exceeded_current_limit(database) -> None:
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(27004)
    original_service = BatchDownloadService(
        database, _CollectionResolver(_collection("one", "two")), max_items=2
    )
    original = await original_service.expand(
        user_id=user.id,
        confirmation_id="original-retry",
        source_type=BatchSourceType.PLAYLIST,
        source_reference="playlist",
        preferences=UserDownloadPreferences(user.id),
    )
    async with database.transaction() as repositories:
        items = await repositories.batch_download.list_items(original.id)
        await repositories.batch_download.set_item(
            items[0].id,
            status="FAILED",
            now=utc_now(),
            error_code="NETWORK",
            error_message="bounded",
        )
        await repositories.batch_download.mark_status(original.id, BatchStatus.PARTIAL, utc_now())
    retry = await BatchDownloadService(
        database, _CollectionResolver(_collection("one")), max_items=1
    ).retry_failed(original.id, user_id=user.id)
    assert retry is not None and retry.total_items == 1


@pytest.mark.asyncio
async def test_storage_preflight_cleans_safe_stale_artifact_or_defers_active_artifact(
    database, tmp_path: Path
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifacts = DownloadArtifactManager(artifact_root, ActiveArtifactRegistry())
    stale_root = artifact_root / ("a" * 32)
    artifact_root.mkdir()
    stale_root.mkdir()
    (stale_root / "stale").write_bytes(b"x" * 8)
    os.utime(stale_root, (0, 0))
    cleanup = StaleArtifactCleanupService(
        database,
        artifact_root,
        artifacts.registry,
        stale_after_seconds=1,
        clock=lambda: 9_999_999_999,
    )
    guard = TemporaryDiskGuard(
        artifact_root, 0, maximum_usage_bytes=4, disk_usage=lambda _: _Usage(100)
    )
    backend = DownloadWorkerBackend(
        database,
        object(),
        artifacts,
        disk_guard=guard,
        artifact_cleanup=cleanup,  # type: ignore[arg-type]
    )
    summary = await cleanup.scan()
    assert summary.stale_candidates == 1
    await backend._ensure_storage_available()
    assert not stale_root.exists()

    active_id, active_root = artifacts.create_job()
    (active_root / "active").write_bytes(b"x" * 8)
    os.utime(active_root, (0, 0))
    with pytest.raises(DownloadPipelineError) as raised:
        await backend._ensure_storage_available()
    assert raised.value.code.value == "TEMP_STORAGE_UNAVAILABLE"
    assert active_root.exists()
    artifacts.release(active_id)


async def _limited(limiter: ProviderRateLimiter, provider: MusicProviderName) -> None:
    async with limiter.operation(provider):
        return


class _QueueSnapshot:
    async def snapshot(self) -> QueueRuntimeSnapshot:
        workers = WorkerPoolSnapshot(1, 1, 1, 1)
        return QueueRuntimeSnapshot(
            download=workers,
            upload=workers,
            download_jobs=QueueStatusCounts(queued=1),
            upload_jobs=QueueStatusCounts(),
        )


class _CollectionResolver:
    def __init__(self, collection: ResolvedCollection) -> None:
        self.collection = collection

    async def resolve_collection(
        self, source_type: BatchSourceType, source_reference: str
    ) -> ResolvedCollection:
        del source_type, source_reference
        return self.collection


class _AlbumResolver:
    def __init__(self, snapshot: AlbumSnapshot) -> None:
        self.snapshot = snapshot

    async def resolve_album(self, url: str) -> AlbumSnapshot:
        del url
        return self.snapshot


def _collection(*values: str) -> ResolvedCollection:
    return ResolvedCollection(
        source_type=BatchSourceType.PLAYLIST,
        provider=MusicProviderName.TIDAL,
        collection_id="stage27",
        source_reference="stage27",
        title="Stage 27",
        creator="Tests",
        items=tuple(
            ResolvedCollectionItem(position=index, provider_media_id=value, title=value)
            for index, value in enumerate(values, 1)
        ),
    )


def _album(*values: str) -> AlbumSnapshot:
    return AlbumSnapshot(
        provider=MusicProviderName.TIDAL,
        provider_album_id="stage27",
        source_url="https://example.invalid/album",
        title="Stage 27",
        artist="Tests",
        tracks=tuple(
            AlbumTrackSnapshot(provider_track_id=value, position=index)
            for index, value in enumerate(values, 1)
        ),
    )
