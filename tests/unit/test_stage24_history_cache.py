from __future__ import annotations

from types import SimpleNamespace

from app.core.telegram_artifact_cache import TelegramCacheKey
from app.telegram.presentation import (
    encode_history_batch,
    encode_history_list,
    encode_history_repeat,
    encode_history_track,
    parse_history_callback,
)


def _request(**overrides: object) -> SimpleNamespace:
    values = {
        "provider": "spotify",
        "provider_media_id": "track-1",
        "effective_quality": "lossless",
        "effective_format": "flac",
        "delivery_mode": "audio",
        "embed_metadata": True,
        "embed_cover": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_stage24_cache_fingerprint_is_stable_and_artifact_based() -> None:
    first = TelegramCacheKey.from_request(_request())
    second = TelegramCacheKey.from_request(_request())
    changed = TelegramCacheKey.from_request(_request(embed_cover=False))
    assert first is not None and first.fingerprint == second.fingerprint
    assert first.fingerprint != changed.fingerprint
    assert '"provider":"spotify"' in first.canonical_json()


def test_stage24_cache_key_rejects_incomplete_profile() -> None:
    assert TelegramCacheKey.from_request(_request(effective_format=None)) is None


def test_stage24_history_callbacks_are_compact_and_strict() -> None:
    assert parse_history_callback(encode_history_list()) is not None
    assert parse_history_callback(encode_history_list("2026-01-01T00:00:00+00:00|4")).cursor
    assert parse_history_callback(encode_history_track(4)).identifier == 4
    assert parse_history_callback(encode_history_batch(5)).action == "batch"
    assert parse_history_callback(encode_history_repeat(6)).action == "repeat"
    assert parse_history_callback("h24:track:not-an-id") is None
    assert parse_history_callback("h24:repeat:0") is None
