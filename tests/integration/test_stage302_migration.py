"""Stage 30.2 migration upgrade and guarded downgrade regressions."""

import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from app.config import get_settings


def _config(database_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    return Config("alembic.ini")


def test_stage302_aac128_round_trip_and_guarded_downgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "stage302.db"
    config = _config(database_path, monkeypatch)
    try:
        command.upgrade(config, "head")
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "INSERT INTO tracks (id, title, created_at, updated_at) "
                "VALUES (1, 'Stage 30.2', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "INSERT INTO download_jobs "
                "(id, track_id, quality_profile, status, attempt_count, queued_at, available_at, "
                "cancel_requested, created_at, updated_at) VALUES "
                "(1, 1, 'AAC_128', 'QUEUED', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
            assert connection.execute(
                "SELECT quality_profile FROM download_jobs WHERE id = 1"
            ).fetchone() == ("AAC_128",)
        with pytest.raises(RuntimeError, match="contains AAC_128 rows"):
            command.downgrade(config, "20260902_0019")
        command.upgrade(config, "head")
    finally:
        get_settings.cache_clear()


def test_stage302_downgrade_without_aac128_rows_then_reupgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "stage302-empty.db"
    config = _config(database_path, monkeypatch)
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "20260902_0019")
        command.upgrade(config, "head")
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "INSERT INTO tracks (id, title, created_at, updated_at) "
                "VALUES (1, 'Stage 30.2', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "INSERT INTO download_jobs "
                "(id, track_id, quality_profile, status, attempt_count, queued_at, available_at, "
                "cancel_requested, created_at, updated_at) VALUES "
                "(1, 1, 'MP3_128', 'QUEUED', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
    finally:
        get_settings.cache_clear()
