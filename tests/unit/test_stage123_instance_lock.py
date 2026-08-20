from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.config import Settings
from app.main import check_runtime, run_bot
from app.services.instance_lock import (
    ApplicationInstanceAlreadyRunningError,
    ApplicationInstanceLock,
    instance_lock_path,
)


def test_instance_lock_first_second_stale_file_and_release(tmp_path: Path) -> None:
    path = tmp_path / "runtime.db.instance.lock"
    path.write_text("pid=stale\n", encoding="ascii")
    first = ApplicationInstanceLock(path)
    second = ApplicationInstanceLock(path)
    first.acquire()
    try:
        with pytest.raises(ApplicationInstanceAlreadyRunningError):
            second.acquire()
        assert second.probe_active()
    finally:
        first.release()
    second.acquire()
    second.release()
    assert not ApplicationInstanceLock(path).probe_active()


def test_same_object_double_acquire_is_rejected(tmp_path: Path) -> None:
    lock = ApplicationInstanceLock(tmp_path / "one.lock")
    lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="already acquired"):
            lock.acquire()
    finally:
        lock.release()


def test_lock_path_is_derived_beside_sqlite_database(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'app.db').as_posix()}"
    assert instance_lock_path(url) == (tmp_path / "app.db.instance.lock").resolve()


def test_process_death_releases_os_lock(tmp_path: Path) -> None:
    path = tmp_path / "process.lock"
    code = (
        "import sys,time; "
        "from pathlib import Path; "
        "from app.services.instance_lock import ApplicationInstanceLock; "
        "lock=ApplicationInstanceLock(Path(sys.argv[1])); "
        "lock.acquire(); print('READY', flush=True); time.sleep(60)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "READY"
        assert ApplicationInstanceLock(path).probe_active()
    finally:
        process.kill()
        process.wait(timeout=10)
    recovered = ApplicationInstanceLock(path)
    recovered.acquire()
    recovered.release()


async def test_partial_startup_failure_releases_lock_and_lock_precedes_schema(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "partial.db"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{database_path.as_posix()}",
        temp_dir=tmp_path / "temp",
    )
    observed_active = False

    async def fail_schema(database: object) -> None:
        nonlocal observed_active
        del database
        observed_active = ApplicationInstanceLock.from_database_url(
            settings.database_url
        ).probe_active()
        raise RuntimeError("controlled startup failure")

    with patch("app.main.require_current_schema", new=fail_schema):
        with pytest.raises(RuntimeError, match="controlled"):
            await run_bot(settings)
    assert observed_active
    assert not ApplicationInstanceLock.from_database_url(settings.database_url).probe_active()


async def test_check_runtime_never_acquires_instance_lock(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'check.db').as_posix()}",
        temp_dir=tmp_path / "temp",
        bot_token="123456:TEST_TOKEN",
        telegram_cache_chat_id=-100123,
    )
    with (
        patch("app.main.require_current_schema", new_callable=AsyncMock),
        patch.object(ApplicationInstanceLock, "acquire") as acquire,
    ):
        await check_runtime(settings)
    acquire.assert_not_called()
