"""Regression coverage for Stage 30.5.6 release-hardening assets."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_packaging_ignores_generated_stage_artifacts_and_validation_environments() -> None:
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for required in (
        ".tmp",
        ".pytest-tmp",
        ".venv*",
        ".codex-venv",
        ".validation-venv",
        "graphify-out",
        "data",
        "temp",
    ):
        assert any(item.rstrip("/") == required for item in ignored)


def test_current_provider_document_declares_bandcamp_contract_and_smokes() -> None:
    document = (ROOT / "docs" / "stage30-provider-platform.md").read_text(encoding="utf-8")
    for required in (
        "Spotify, Deezer, Tidal, YouTube Music, Qobuz, Apple Music,\nand Bandcamp",
        "Tidal, Deezer,\nSpotify, Qobuz, Apple Music",
        "SoundCloud remains deferred",
        "public MP3/128",
        "`AAC_128`, `AAC_256`,\n`MP3_128`, `MP3_320`, and `LOSSLESS`",
        "`MusicProvider.check_source()` remains the authoritative",
        "APPLE_MUSIC_MEDIA_USER_TOKEN",
        "STAGE30_PROVIDER_PLATFORM_CONTAINER_VALIDATION=PASS",
    ):
        assert required in document


def test_validation_script_rebuilds_current_runtime_and_validation_images() -> None:
    script = (ROOT / "scripts" / "validate-production.sh").read_text(encoding="utf-8")
    for required in (
        'IMAGE="${MUSICBOT_IMAGE:-musicbot-downloader:stage30.5.6}"',
        "docker build --target runtime",
        "docker build --target validation",
        "UV_PROJECT_ENVIRONMENT",
        "uv run ruff format --check .",
        'uv run pytest -m "not external" -p no:cacheprovider',
        "STAGE30_PROVIDER_PLATFORM_CONTAINER_VALIDATION=PASS",
    ):
        assert required in script
