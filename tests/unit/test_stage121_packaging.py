from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dockerfile_uses_locked_python312_non_root_runtime() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12-slim-bookworm AS builder" in dockerfile
    assert "FROM python:3.12-slim-bookworm AS runtime-base" in dockerfile
    assert "FROM runtime-base AS runtime" in dockerfile
    assert "uv sync --locked --no-dev --extra onthespot" in dockerfile
    assert "ffmpeg" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert 'CMD ["python", "-m", "app.main"]' in dockerfile
    assert "COPY .env" not in dockerfile
    production_target = dockerfile.rsplit("FROM runtime-base AS runtime", maxsplit=1)[1]
    assert "pytest" not in production_target


def test_validation_target_is_opt_in_and_locked() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM runtime-base AS validation" in dockerfile
    assert "UV_PROJECT_ENVIRONMENT=/opt/venv uv sync" in dockerfile
    assert "--locked --extra dev --extra onthespot --no-editable" in dockerfile
    assert 'CMD ["pytest", "-m", "not external"' in dockerfile


def test_dockerignore_excludes_secrets_databases_and_build_noise() -> None:
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    for required in (
        ".git",
        ".venv",
        ".env",
        ".env.*",
        "*.db",
        "data",
        "temp",
        "otsconfig.json",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    ):
        assert required in ignored


def test_compose_is_single_instance_private_and_unprivileged() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert compose.count("  musicbot:") == 1
    assert "replicas:" not in compose
    assert "ports:" not in compose
    assert "privileged:" not in compose
    assert "no-new-privileges:true" in compose
    assert "- ALL" in compose
    assert "sqlite+aiosqlite:////data/musicbot.db" in compose
    assert "TEMP_DIR: /tmp/musicbot" in compose
    assert "musicbot-data:/data" in compose
