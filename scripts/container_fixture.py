"""Non-production fixture used by the Stage 12.4 Linux container drill."""

from __future__ import annotations

import argparse
import os
import signal
import sqlite3
import time
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.services.instance_lock import (
    ApplicationInstanceAlreadyRunningError,
    ApplicationInstanceLock,
    sqlite_database_path,
)


def _database_path() -> Path:
    return sqlite_database_path(os.environ["DATABASE_URL"])


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(_database_path(), timeout=5.0)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _current_alembic_head() -> str:
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"expected exactly one Alembic head, got {heads!r}")
    return heads[0]


def _seed_preupgrade() -> None:
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO users (
                telegram_id, username, first_name, role, telegram_language_code,
                preferred_locale, preferred_quality_profile, is_banned,
                last_seen_at, created_at, updated_at
            ) VALUES (
                1204001, 'preupgrade', 'Release', 'USER', 'en', 'en',
                'MP3_320', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """
        )


def _verify_preupgrade() -> None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT username, preferred_quality_profile FROM users WHERE telegram_id=1204001"
        ).fetchone()
    if row != ("preupgrade", "MP3_320"):
        raise RuntimeError(f"pre-upgrade row mismatch: {row!r}")
    print("pre-upgrade-row=preserved")


def _seed_durable() -> None:
    with _connect() as connection:
        track_id = connection.execute(
            """
            INSERT INTO tracks (
                title, artist, normalized_title, normalized_artist,
                created_at, updated_at
            ) VALUES (
                'Release Fixture', 'Stage 12.4', 'release fixture', 'stage 12.4',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """
        ).lastrowid
        if track_id is None:
            raise RuntimeError("track fixture was not inserted")
        source_id = connection.execute(
            """
            INSERT INTO track_sources (
                track_id, provider, provider_track_id, url, metadata_json,
                created_at, updated_at
            ) VALUES (?, 'spotify', 'stage124-source',
                'https://open.spotify.com/track/stage124', '{}',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (track_id,),
        ).lastrowid
        connection.execute(
            """
            INSERT INTO telegram_file_cache (
                telegram_bot_id, track_id, quality_profile, telegram_file_id,
                telegram_file_unique_id, telegram_media_kind, cache_chat_id,
                cache_message_id, file_size_bytes, source_track_source_id,
                source_provider, source_provider_track_id, operation, transcoded,
                status, created_at, updated_at
            ) VALUES (
                1204002, ?, 'MP3_320', 'stage124-file-id', 'stage124-unique-id',
                'AUDIO', -1001204, 1, 1024, ?, 'spotify', 'stage124-source',
                'DIRECT', 0, 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """,
            (track_id, source_id),
        )
        connection.execute(
            """
            INSERT INTO runtime_settings (
                id, download_workers, upload_workers, created_at, updated_at
            ) VALUES (1, 2, 3, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        )
        download_id = connection.execute(
            """
            INSERT INTO download_jobs (
                track_id, quality_profile, status, attempt_count, queued_at,
                available_at, started_at, finished_at, cancel_requested,
                created_at, updated_at
            ) VALUES (
                ?, 'MP3_320', 'SUCCEEDED', 1, CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """,
            (track_id,),
        ).lastrowid
        if download_id is None:
            raise RuntimeError("download history fixture was not inserted")
        connection.execute(
            """
            INSERT INTO upload_jobs (
                download_job_id, track_id, quality_profile, status,
                artifact_job_id, artifact_path, source_track_source_id,
                source_provider, source_provider_track_id, operation,
                transcoded, attempt_count, queued_at, available_at, started_at,
                finished_at, cancel_requested, created_at, updated_at
            ) VALUES (
                ?, ?, 'MP3_320', 'SUCCEEDED', ?, ?, ?, 'spotify',
                'stage124-source', 'DIRECT', 0, 1, CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """,
            (
                download_id,
                track_id,
                "1234567890abcdef1234567890abcdef",
                "1234567890abcdef1234567890abcdef/final.mp3",
                source_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO job_subscribers (
                id, download_job_id, status, request_key, completed_at,
                created_at, updated_at
            ) VALUES (
                '12345678-1234-1234-1234-123456789012', ?, 'READY',
                'stage124-subscriber', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            )
            """,
            (download_id,),
        )
        connection.execute(
            """
            INSERT INTO deep_link_registry (
                telegram_bot_id, token, target_type, track_id, status,
                idempotency_key, request_fingerprint, created_at, updated_at
            ) VALUES (
                1204002, ?, 'TRACK', ?, 'ACTIVE', 'stage124-idempotency', ?,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """,
            ("d1_" + "a" * 32, track_id, "b" * 64),
        )
        connection.execute(
            """
            INSERT INTO operational_audit_events (
                occurred_at, event_type, actor_kind, target_kind, target_id,
                request_id, details_json
            ) VALUES (
                CURRENT_TIMESTAMP, 'CRASH_RECOVERY_COMPLETED', 'SYSTEM',
                'RECOVERY', 'startup', 'stage124', '{"fixture":true}'
            )
            """
        )
    print("durable-fixture=seeded")


def _verify_durable(expected_username: str) -> None:
    expected = {
        "users": 1,
        "tracks": 1,
        "track_sources": 1,
        "telegram_file_cache": 1,
        "runtime_settings": 1,
        "download_jobs": 1,
        "upload_jobs": 1,
        "job_subscribers": 1,
        "deep_link_registry": 1,
        "operational_audit_events": 1,
    }
    with _connect() as connection:
        observed = {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in expected
        }
        username = connection.execute(
            "SELECT username FROM users WHERE telegram_id=1204001"
        ).fetchone()
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    if observed != expected:
        raise RuntimeError(f"durable row counts mismatch: {observed!r}")
    if username != (expected_username,):
        raise RuntimeError(f"username mismatch: {username!r}")
    if str(journal_mode).lower() != "wal" or foreign_keys != 1 or busy_timeout != 5000:
        raise RuntimeError(
            f"SQLite pragma mismatch: {journal_mode!r}, {foreign_keys!r}, {busy_timeout!r}"
        )
    if integrity != "ok" or revision != _current_alembic_head():
        raise RuntimeError(f"database validation failed: {integrity!r}, {revision!r}")
    media_suffixes = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".webm"}
    persistent_media = [
        str(path)
        for path in _database_path().parent.rglob("*")
        if path.is_file() and path.suffix.lower() in media_suffixes
    ]
    if persistent_media:
        raise RuntimeError(f"unexpected persistent media: {persistent_media!r}")
    print(
        "durable-fixture=verified "
        f"journal_mode={journal_mode} foreign_keys={foreign_keys} "
        f"busy_timeout={busy_timeout} revision={revision}"
    )


def _mutate() -> None:
    with _connect() as connection:
        connection.execute(
            "UPDATE users SET username='post-backup', updated_at=CURRENT_TIMESTAMP "
            "WHERE telegram_id=1204001"
        )
    print("durable-fixture=mutated")


def _hold_lock(write: bool) -> None:
    lock = ApplicationInstanceLock.from_database_url(os.environ["DATABASE_URL"])
    lock.acquire()
    connection = _connect() if write else None
    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print("READY", flush=True)
    try:
        while not stopping:
            if connection is not None:
                connection.execute(
                    "UPDATE users SET last_seen_at=CURRENT_TIMESTAMP WHERE telegram_id=1204001"
                )
                connection.commit()
            time.sleep(0.02)
    finally:
        if connection is not None:
            connection.close()
        lock.release()
        print("STOPPED", flush=True)


def _probe_lock() -> None:
    lock = ApplicationInstanceLock.from_database_url(os.environ["DATABASE_URL"])
    try:
        lock.acquire()
    except ApplicationInstanceAlreadyRunningError:
        print("lock=busy")
        raise SystemExit(2) from None
    else:
        lock.release()
        print("lock=available")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "seed-preupgrade",
            "verify-preupgrade",
            "seed-durable",
            "verify-durable",
            "verify-restored",
            "mutate",
            "hold-lock",
            "write-and-hold-lock",
            "probe-lock",
        ),
    )
    args = parser.parse_args()
    operations = {
        "seed-preupgrade": _seed_preupgrade,
        "verify-preupgrade": _verify_preupgrade,
        "seed-durable": _seed_durable,
        "verify-durable": lambda: _verify_durable("preupgrade"),
        "verify-restored": lambda: _verify_durable("preupgrade"),
        "mutate": _mutate,
        "hold-lock": lambda: _hold_lock(False),
        "write-and-hold-lock": lambda: _hold_lock(True),
        "probe-lock": _probe_lock,
    }
    operations[args.command]()


if __name__ == "__main__":
    main()
