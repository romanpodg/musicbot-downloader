from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.core.enums import MusicProviderName
from app.services.provider_limits import ProviderRateLimiter
from app.services.runtime_prerequisites import TemporaryDiskGuard


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
