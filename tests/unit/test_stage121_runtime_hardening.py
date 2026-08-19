from __future__ import annotations

import errno
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.logging import configure_logging, redact_secrets
from app.services.runtime_prerequisites import (
    RuntimePrerequisiteService,
    TemporaryDiskGuard,
)


def test_temp_directory_write_probe_creates_no_persistent_sentinel(tmp_path: Path) -> None:
    target = tmp_path / "runtime-temp"

    report = RuntimePrerequisiteService(
        target,
        ffmpeg_binary="definitely-missing-ffmpeg",
        ffprobe_binary="definitely-missing-ffprobe",
    ).check()

    assert report.temp_dir == target.resolve()
    assert report.ffmpeg_available is False
    assert report.ffprobe_available is False
    assert list(target.iterdir()) == []


def test_temp_directory_write_failure_is_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied(*args: object, **kwargs: object) -> str:
        raise PermissionError("controlled")

    monkeypatch.setattr("app.services.runtime_prerequisites.tempfile.mkdtemp", denied)
    with pytest.raises(OSError, match="TEMP_DIR is not writable") as raised:
        RuntimePrerequisiteService(tmp_path).check()
    assert raised.value.errno == errno.EACCES


@pytest.mark.parametrize(("free", "allowed"), [(101, True), (100, True), (99, False)])
def test_temporary_disk_reserve_boundary(tmp_path: Path, free: int, allowed: bool) -> None:
    guard = TemporaryDiskGuard(
        tmp_path,
        100,
        disk_usage=lambda _: SimpleNamespace(free=free),
    )
    if allowed:
        guard.ensure_available()
    else:
        with pytest.raises(OSError) as raised:
            guard.ensure_available()
        assert raised.value.errno == errno.ENOSPC


def test_secret_redaction_covers_credentials_without_corrupting_ids() -> None:
    secret = "123456:ABCDEF_secret"
    message = (
        f"https://api.telegram.org/bot{secret}/getMe "
        "Authorization: Bearer internal-secret "
        "access_token=provider-secret refresh_token='refresh-secret' "
        'cookie="session-secret" arl=arl-secret '
        '"access_token": "json-provider-secret" track_id=42 job_id=abc123'
    )

    redacted = redact_secrets(message, (secret,))

    for value in (
        secret,
        "internal-secret",
        "provider-secret",
        "refresh-secret",
        "session-secret",
        "arl-secret",
        "json-provider-secret",
    ):
        assert value not in redacted
    assert "track_id=42" in redacted
    assert "job_id=abc123" in redacted


def test_logging_configuration_is_idempotent_and_redacts_formatted_exceptions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(
        _env_file=None,
        bot_token="123456:configured-secret",
        internal_api_token="internal-configured-secret-value-123456",
    )
    configure_logging(settings)
    configure_logging(settings)
    assert len(logging.getLogger().handlers) == 1

    try:
        raise RuntimeError("Authorization: Bearer exception-secret")
    except RuntimeError:
        logging.getLogger("test").exception(
            "failed https://api.telegram.org/bot123456:configured-secret/getMe"
        )

    output = capsys.readouterr().err
    assert "configured-secret" not in output
    assert "exception-secret" not in output
    assert "[REDACTED]" in output
    assert "\\n" in output
