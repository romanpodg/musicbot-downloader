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
                "(id, title, created_at, updated_at) "
                "VALUES (1, 'Track', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
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
        command.downgrade(config, "20260817_0001")
        with sqlite3.connect(database_path) as connection:
            assert connection.execute("SELECT provider FROM track_sources").fetchone() == (
                "APPLE_MUSIC",
            )
    finally:
        get_settings.cache_clear()
