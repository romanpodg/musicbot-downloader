from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

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


def test_stage93_migration_upgrades_0008_preserves_tracks_and_round_trips(
    tmp_path: Path, monkeypatch: object
) -> None:
    database_path = tmp_path / "stage93.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)  # type: ignore[attr-defined]
    get_settings.cache_clear()
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "20260818_0008")
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "INSERT INTO users "
                "(id, telegram_id, role, preferred_quality_profile, is_banned, last_seen_at, "
                "created_at, updated_at) VALUES "
                "(1, 1, 'USER', 'MP3_320', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
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
                "updated_at, card_message_id) VALUES "
                "(1, 100, 1, 1, 10, 1, 'MP3_320', 'AWAITING_ACTION', 0, 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 99)"
            )
        command.upgrade(config, "head")
        with sqlite3.connect(database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            assert {"telegram_album_requests", "telegram_album_items"} <= tables
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info('telegram_delivery_requests')")
            }
            assert "album_item_id" in columns
            assert connection.execute(
                "SELECT source_message_id, album_item_id, card_message_id "
                "FROM telegram_delivery_requests WHERE id=1"
            ).fetchone() == (10, None, 99)
            connection.execute(
                "INSERT INTO telegram_album_requests "
                "(id, telegram_bot_id, user_id, telegram_chat_id, source_message_id, provider, "
                "provider_album_id, title, artist, track_count, quality_profile, status, "
                "created_at, updated_at) VALUES "
                "(1, 100, 1, 1, 11, 'spotify', 'album', 'Album', 'Artist', 1, 'MP3_320', "
                "'PROCESSING', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "INSERT INTO telegram_album_items "
                "(id, album_request_id, position, provider_track_id, selected, "
                "resolution_status, track_id, attempt_count, available_at, created_at, "
                "updated_at) VALUES "
                "(1, 1, 1, 'track', 1, 'ATTACHED', 1, 1, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "INSERT INTO telegram_delivery_requests "
                "(id, telegram_bot_id, user_id, telegram_chat_id, source_message_id, "
                "album_item_id, track_id, quality_profile, status, attempt_count, repair_count, "
                "available_at, created_at, updated_at) VALUES "
                "(2, 100, 1, 1, NULL, 1, 1, 'MP3_320', 'QUEUED', 0, 0, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        command.check(config)
        command.downgrade(config, "20260818_0008")
        with sqlite3.connect(database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            assert "telegram_album_requests" not in tables
            assert "telegram_album_items" not in tables
            assert connection.execute(
                "SELECT id, source_message_id, card_message_id "
                "FROM telegram_delivery_requests ORDER BY id"
            ).fetchall() == [(1, 10, 99)]
        command.upgrade(config, "head")
        command.check(config)
    finally:
        get_settings.cache_clear()


def test_stage11_migration_enforces_registry_target_and_bot_scope(
    tmp_path: Path, monkeypatch: object
) -> None:
    database_path = tmp_path / "stage11.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)  # type: ignore[attr-defined]
    get_settings.cache_clear()
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "head")
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "INSERT INTO tracks (id, title, created_at, updated_at) "
                "VALUES (1, 'Song', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "INSERT INTO deep_link_registry "
                "(telegram_bot_id, token, target_type, track_id, status, idempotency_key, "
                "request_fingerprint, created_at, updated_at) VALUES "
                "(100, 'd1_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', 'TRACK', 1, 'ACTIVE', 'post-1', "
                "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO deep_link_registry "
                    "(telegram_bot_id, token, target_type, track_id, album_provider, "
                    "album_provider_id, status, request_fingerprint, created_at, updated_at) "
                    "VALUES (100, 'd1_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB', 'TRACK', 1, 'spotify', "
                    "'album', 'ACTIVE', "
                    "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            connection.execute(
                "INSERT INTO deep_link_registry "
                "(telegram_bot_id, token, target_type, track_id, status, idempotency_key, "
                "request_fingerprint, created_at, updated_at) VALUES "
                "(200, 'd1_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', 'TRACK', 1, 'ACTIVE', 'post-1', "
                "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        command.check(config)
        command.downgrade(config, "20260818_0009")
        with sqlite3.connect(database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            assert "deep_link_registry" not in tables
        command.upgrade(config, "head")
        command.check(config)
    finally:
        get_settings.cache_clear()


def test_stage123_audit_migration_upgrades_0010_and_round_trips(
    tmp_path: Path, monkeypatch: object
) -> None:
    database_path = tmp_path / "stage123.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)  # type: ignore[attr-defined]
    get_settings.cache_clear()
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "20260820_0010")
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "INSERT INTO users (id, telegram_id, role, is_banned, last_seen_at, "
                "created_at, updated_at) VALUES "
                "(1, 123, 'OWNER', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        command.upgrade(config, "20260820_0011")
        with sqlite3.connect(database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            assert "operational_audit_events" in tables
            connection.execute(
                "INSERT INTO operational_audit_events "
                "(occurred_at, event_type, actor_kind, actor_user_id, target_kind, target_id, "
                "details_json) VALUES (CURRENT_TIMESTAMP, 'ADMIN_PROMOTED', 'TELEGRAM_USER', "
                "1, 'USER', '1', '{\"new_role\":\"ADMIN\"}')"
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO operational_audit_events "
                    "(occurred_at, event_type, actor_kind, actor_user_id) VALUES "
                    "(CURRENT_TIMESTAMP, 'ADMIN_PROMOTED', 'SYSTEM', 1)"
                )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO operational_audit_events "
                    "(occurred_at, event_type, actor_kind, details_json) VALUES "
                    "(CURRENT_TIMESTAMP, 'CRASH_RECOVERY_COMPLETED', 'SYSTEM', ?)",
                    ("x" * 4097,),
                )
            indexes = {
                row[1]
                for row in connection.execute(
                    "PRAGMA index_list('operational_audit_events')"
                ).fetchall()
            }
            assert {
                "ix_operational_audit_occurred",
                "ix_operational_audit_event_occurred",
                "ix_operational_audit_actor_occurred",
            } <= indexes
        command.downgrade(config, "20260820_0010")
        with sqlite3.connect(database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            assert "operational_audit_events" not in tables
            assert connection.execute("SELECT role FROM users WHERE id=1").fetchone() == ("OWNER",)
        command.upgrade(config, "head")
        command.check(config)
    finally:
        get_settings.cache_clear()


def test_stage19_migration_is_independent_and_round_trips_with_legacy_backfill(
    tmp_path: Path, monkeypatch: object
) -> None:
    database_path = tmp_path / "stage19.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)  # type: ignore[attr-defined]
    get_settings.cache_clear()
    config = Config("alembic.ini")
    try:
        script = ScriptDirectory.from_config(config)
        assert script.get_heads() == ["20260830_0013"]
        command.upgrade(config, "head")
        with sqlite3.connect(database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            assert {"telegram_chat_policies", "telegram_channel_bindings"} <= tables
        command.downgrade(config, "20260820_0011")
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "INSERT INTO users (id, telegram_id, role, is_banned, last_seen_at, "
                "created_at, updated_at) VALUES "
                "(1, 1, 'USER', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "INSERT INTO tracks (id, title, created_at, updated_at) VALUES "
                "(1, 'Song', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "INSERT INTO telegram_delivery_requests "
                "(id, telegram_bot_id, user_id, telegram_chat_id, source_message_id, track_id, "
                "quality_profile, status, attempt_count, repair_count, available_at, "
                "created_at, updated_at) VALUES "
                "(1, 100, 1, 1, 9, 1, NULL, 'AWAITING_QUALITY', 0, 0, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        command.upgrade(config, "head")
        with sqlite3.connect(database_path) as connection:
            assert connection.execute(
                "SELECT delivery_chat_id, delivery_target_type "
                "FROM telegram_delivery_requests WHERE id=1"
            ).fetchone() == (1, "PRIVATE_USER")
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO telegram_chat_policies "
                    "(chat_id, allow_downloads, delivery_mode, created_at, updated_at) "
                    "VALUES (-1, 1, 'INVALID', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
        command.check(config)
        command.downgrade(config, "20260820_0011")
        command.upgrade(config, "head")
        command.check(config)
    finally:
        get_settings.cache_clear()
