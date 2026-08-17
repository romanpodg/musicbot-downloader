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


def test_stage8_migration_upgrades_0005_and_round_trips(
    tmp_path: Path, monkeypatch: object
) -> None:
    database_path = tmp_path / "stage8.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)  # type: ignore[attr-defined]
    get_settings.cache_clear()
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "20260818_0005")
        command.upgrade(config, "head")
        with sqlite3.connect(database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            assert "telegram_file_cache" in tables
            cache_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info('telegram_file_cache')").fetchall()
            }
            assert {
                "telegram_bot_id",
                "track_id",
                "quality_profile",
                "telegram_file_id",
                "telegram_file_unique_id",
                "source_provider",
                "source_provider_track_id",
                "operation",
                "output_codec",
                "output_container",
                "status",
            } <= cache_columns
            upload_columns = {
                row[1] for row in connection.execute("PRAGMA table_info('upload_jobs')").fetchall()
            }
            assert {"source_provider", "operation", "file_size_bytes", "encoder"} <= upload_columns
            indexes = connection.execute("PRAGMA index_list('telegram_file_cache')").fetchall()
            assert any(row[2] for row in indexes)
            assert any(row[1] == "ix_telegram_file_cache_status_created" for row in indexes)
        command.check(config)
        command.downgrade(config, "20260818_0005")
        with sqlite3.connect(database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            assert "telegram_file_cache" not in tables
            upload_columns = {
                row[1] for row in connection.execute("PRAGMA table_info('upload_jobs')").fetchall()
            }
            assert "source_provider" not in upload_columns
        command.upgrade(config, "head")
        command.check(config)
    finally:
        get_settings.cache_clear()


def test_stage9_migration_upgrades_0006_and_round_trips(
    tmp_path: Path, monkeypatch: object
) -> None:
    database_path = tmp_path / "stage9.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)  # type: ignore[attr-defined]
    get_settings.cache_clear()
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "20260818_0006")
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "INSERT INTO users "
                "(telegram_id, role, default_quality, is_banned, last_seen_at, "
                "created_at, updated_at) "
                "VALUES (1, 'USER', 'AAC_256', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP)"
            )
        command.upgrade(config, "head")
        with sqlite3.connect(database_path) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info('users')").fetchall()
            }
            assert "preferred_quality_profile" in columns
            assert "default_quality" not in columns
            assert connection.execute(
                "SELECT preferred_quality_profile FROM users WHERE telegram_id = 1"
            ).fetchone() == ("AAC_256",)
            indexes = {
                row[1]
                for row in connection.execute(
                    "PRAGMA index_list('telegram_delivery_requests')"
                ).fetchall()
            }
            assert {
                "ix_telegram_delivery_claim",
                "ix_telegram_delivery_subscriber",
                "ix_telegram_delivery_user",
            } <= indexes
        command.check(config)
        command.downgrade(config, "20260818_0006")
        with sqlite3.connect(database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            assert "telegram_delivery_requests" not in tables
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info('users')").fetchall()
            }
            assert "default_quality" in columns
        command.upgrade(config, "head")
        command.check(config)
    finally:
        get_settings.cache_clear()


def test_stage92_migration_upgrades_0007_and_round_trips(
    tmp_path: Path, monkeypatch: object
) -> None:
    database_path = tmp_path / "stage92.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)  # type: ignore[attr-defined]
    get_settings.cache_clear()
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "20260818_0007")
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "INSERT INTO users "
                "(telegram_id, role, preferred_quality_profile, is_banned, last_seen_at, "
                "created_at, updated_at) VALUES "
                "(1, 'USER', 'MP3_320', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "INSERT INTO tracks (id, title, artist, normalized_title, normalized_artist, "
                "created_at, updated_at) VALUES "
                "(1, 'Song', 'Artist', 'song', 'artist', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "INSERT INTO telegram_delivery_requests "
                "(id, telegram_bot_id, user_id, telegram_chat_id, source_message_id, track_id, "
                "quality_profile, status, attempt_count, repair_count, available_at, created_at, "
                "updated_at) VALUES "
                "(1, 100, 1, 1, 1, 1, NULL, 'AWAITING_QUALITY', 0, 0, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        command.upgrade(config, "head")
        with sqlite3.connect(database_path) as connection:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info('telegram_delivery_requests')"
                ).fetchall()
            }
            assert "card_message_id" in columns
            assert connection.execute(
                "SELECT status FROM telegram_delivery_requests WHERE id = 1"
            ).fetchone() == ("AWAITING_QUALITY",)
            connection.execute(
                "INSERT INTO telegram_delivery_requests "
                "(id, telegram_bot_id, user_id, telegram_chat_id, source_message_id, track_id, "
                "quality_profile, status, attempt_count, repair_count, available_at, created_at, "
                "updated_at, card_message_id) VALUES "
                "(2, 100, 1, 1, 2, 1, 'MP3_320', 'AWAITING_ACTION', 0, 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 99)"
            )
            connection.execute("DELETE FROM telegram_delivery_requests WHERE id = 2")
        command.check(config)
        command.downgrade(config, "20260818_0007")
        with sqlite3.connect(database_path) as connection:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info('telegram_delivery_requests')"
                ).fetchall()
            }
            assert "card_message_id" not in columns
            assert connection.execute(
                "SELECT status FROM telegram_delivery_requests WHERE id = 1"
            ).fetchone() == ("AWAITING_QUALITY",)
        command.upgrade(config, "head")
        command.check(config)
    finally:
        get_settings.cache_clear()
