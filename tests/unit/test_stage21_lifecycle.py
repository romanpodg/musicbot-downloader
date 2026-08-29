from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from app.core.delivery_targets import PrivateUserTarget
from app.core.download import DownloadDeliveryTarget, DownloadRequest
from app.core.download_lifecycle import DownloadLifecycle, InvalidDownloadTransition
from app.core.enums import DownloadFailureCode, DownloadJobStatus, DownloadPhase, MusicProviderName
from app.core.search import Artist, Track
from app.core.telegram_context import TelegramChatType, TelegramContext
from app.services.download_lifecycle import DownloadLifecycleService, DownloadWorkspaceManager


def test_stage21_state_machine_rejects_terminal_reactivation() -> None:
    DownloadLifecycle.validate(DownloadJobStatus.PENDING, DownloadJobStatus.QUEUED)
    DownloadLifecycle.validate(DownloadJobStatus.RUNNING, DownloadJobStatus.DELIVERING)
    with pytest.raises(InvalidDownloadTransition):
        DownloadLifecycle.validate(DownloadJobStatus.SUCCEEDED, DownloadJobStatus.RUNNING)
    with pytest.raises(InvalidDownloadTransition):
        DownloadLifecycle.validate(DownloadJobStatus.FAILED, DownloadJobStatus.QUEUED)
    with pytest.raises(InvalidDownloadTransition):
        DownloadLifecycle.validate(DownloadJobStatus.CANCELLED, DownloadJobStatus.RUNNING)


def test_stage21_workspace_isolated_and_orphans_removed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    manager = DownloadWorkspaceManager(tmp_path / "musicbot")
    manager.create(1).joinpath("artifact").write_bytes(b"x")
    manager.create(2)
    manager.cleanup_orphans({2})
    assert not (tmp_path / "musicbot" / "1").exists()
    assert (tmp_path / "musicbot" / "2").exists()


async def test_stage21_concurrent_confirmation_admission_is_idempotent(database) -> None:  # type: ignore[no-untyped-def]
    async with database.transaction() as repositories:
        await repositories.users.create_user(21001)
        track = await repositories.tracks.create_track(title="Lifecycle", artist="Test")
    context = TelegramContext(21001, 21001, TelegramChatType.PRIVATE)
    request = DownloadRequest(
        21001,
        Track(
            id="search:spotify:lifecycle",
            title="Lifecycle",
            artists=(Artist("Test"),),
            provider=MusicProviderName.SPOTIFY,
            provider_track_id="lifecycle",
        ),
        confirmation_id="stage21-confirmation",
    )
    target = DownloadDeliveryTarget(21001, context, PrivateUserTarget(21001), 2101)
    service = DownloadLifecycleService(database)
    admitted = await asyncio.gather(
        service.admit(
            confirmation_id="stage21-confirmation",
            request=request,
            canonical_track_id=track.id,
            target=target,
        ),
        service.admit(
            confirmation_id="stage21-confirmation",
            request=request,
            canonical_track_id=track.id,
            target=target,
        ),
    )
    assert admitted[0].request.id == admitted[1].request.id
    async with database.transaction() as repositories:
        stored = await repositories.download_lifecycle.get_by_confirmation("stage21-confirmation")
        assert stored is not None
        assert (
            await repositories.download_lifecycle.get_job(admitted[0].job.id)
        ).request_id == stored.id  # type: ignore[union-attr]


async def test_stage21_claim_lease_retry_and_recovery(database) -> None:  # type: ignore[no-untyped-def]
    async with database.transaction() as repositories:
        await repositories.users.create_user(21002)
        track = await repositories.tracks.create_track(title="Lease", artist="Test")
    service = DownloadLifecycleService(database, clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    context = TelegramContext(21002, 21002, TelegramChatType.PRIVATE)
    request = DownloadRequest(
        21002,
        Track("t", "Lease", (Artist("Test"),), MusicProviderName.SPOTIFY, "lease"),
        confirmation_id="lease-confirmation",
    )
    target = DownloadDeliveryTarget(21002, context, PrivateUserTarget(21002), 2102)
    admitted = await service.admit(
        confirmation_id="lease-confirmation",
        request=request,
        canonical_track_id=track.id,
        target=target,
    )
    claimed = await service.claim("worker-a")
    assert claimed is not None and claimed.status is DownloadJobStatus.RUNNING
    assert await service.set_phase(claimed.id, "worker-a", DownloadPhase.DOWNLOADING)
    assert (
        await service.schedule_retry(claimed.id, "worker-a", DownloadFailureCode.NETWORK)
        is DownloadJobStatus.RETRY_WAIT
    )
    async with database.transaction() as repositories:
        job = await repositories.download_lifecycle.get_job(admitted.job.id)
        assert job is not None and job.retry_at is not None
