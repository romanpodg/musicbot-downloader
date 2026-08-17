from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic.config import Config

from alembic import command
from app.config import get_settings


def test_provider_enum_migration_converts_existing_rows(
    tmp_path: Path, monkeypatch: object
) -> None:
    # Kept synchronous because Alembic owns its event loop.
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)  # type: ignore[attr-defined]
    get_settings.cache_clear()
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "20260817_0001")
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "INSERT INTO tracks "
                "(id, title, isrc, created_at, updated_at) "
                "VALUES (1, 'Song - Live', 'us-abc-12-34567', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "INSERT INTO track_sources "
                "(track_id, provider, provider_track_id, metadata_json, created_at, updated_at) "
                "VALUES (1, 'APPLE_MUSIC', '456', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        command.upgrade(config, "head")
        with sqlite3.connect(database_path) as connection:
            assert connection.execute("SELECT provider FROM track_sources").fetchone() == (
                "apple_music",
            )
            assert connection.execute(
                "SELECT normalized_title, normalized_artist, isrc FROM tracks"
            ).fetchone() == ("song", None, "USABC1234567")
            indexes = {
                row[1] for row in connection.execute("PRAGMA index_list('tracks')").fetchall()
            }
            assert "ix_tracks_normalized_artist_title" in indexes
        command.downgrade(config, "20260817_0001")
        with sqlite3.connect(database_path) as connection:
            assert connection.execute("SELECT provider FROM track_sources").fetchone() == (
                "APPLE_MUSIC",
            )
    finally:
        get_settings.cache_clear()


def test_stage7_migration_creates_clean_queue_schema(tmp_path: Path, monkeypatch: object) -> None:
    database_path = tmp_path / "stage7.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)  # type: ignore[attr-defined]
    get_settings.cache_clear()
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "head")
        with sqlite3.connect(database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            assert {"download_jobs", "upload_jobs", "runtime_settings"} <= tables
            download_indexes = {
                row[1]
                for row in connection.execute("PRAGMA index_list('download_jobs')").fetchall()
            }
            upload_indexes = {
                row[1] for row in connection.execute("PRAGMA index_list('upload_jobs')").fetchall()
            }
            assert "ix_download_jobs_claim" in download_indexes
            assert "ix_upload_jobs_claim" in upload_indexes
            assert any(row[2] for row in connection.execute("PRAGMA index_list('upload_jobs')"))
        command.check(config)
        command.downgrade(config, "20260817_0003")
        with sqlite3.connect(database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            assert "download_jobs" not in tables
        command.upgrade(config, "head")
    finally:
        get_settings.cache_clear()


def test_stage71_migration_upgrades_stage7_and_round_trips(
    tmp_path: Path, monkeypatch: object
) -> None:
    database_path = tmp_path / "stage71.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)  # type: ignore[attr-defined]
    get_settings.cache_clear()
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "20260818_0004")
        command.upgrade(config, "head")
        with sqlite3.connect(database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            assert {"download_flights", "job_subscribers"} <= tables
            flight_indexes = connection.execute("PRAGMA index_list('download_flights')").fetchall()
            subscriber_indexes = {
                row[1]
                for row in connection.execute("PRAGMA index_list('job_subscribers')").fetchall()
            }
            assert sum(bool(row[2]) for row in flight_indexes) == 2
            assert "ix_job_subscribers_job_status" in subscriber_indexes
            assert "ix_job_subscribers_status_created" in subscriber_indexes
            subscriber_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info('job_subscribers')").fetchall()
            }
            assert "artifact_path" not in subscriber_columns
            assert "artifact_job_id" not in subscriber_columns
        command.check(config)
        command.downgrade(config, "20260818_0004")
        with sqlite3.connect(database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            assert "download_flights" not in tables
            assert "job_subscribers" not in tables
            assert "download_jobs" in tables
        command.upgrade(config, "head")
    finally:
        get_settings.cache_clear()
