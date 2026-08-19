"""Container-safe application logging and centralized secret redaction."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable

from app.config import Settings

REDACTED = "[REDACTED]"
_SENSITIVE_VALUE = re.compile(
    r"(?i)([\"']?\b(?:access[_-]?token|refresh[_-]?token|internal[_-]?api[_-]?token|"
    r"bot[_-]?token|authorization|cookie|set-cookie|arl)\b[\"']?\s*[=:]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}&]+)"
)
_BEARER = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+")
_TELEGRAM_URL = re.compile(r"(?i)(https://api\.telegram\.org/bot)[^/\s]+")
_CORRELATION_FIELDS = (
    "download_job_id",
    "upload_job_id",
    "subscriber_id",
    "telegram_delivery_request_id",
    "album_request_id",
    "deep_link_registry_id",
    "job_id",
    "track_id",
    "worker_id",
    "request_id",
)


def redact_secrets(value: object, secrets: Iterable[str] = ()) -> str:
    """Return log-safe text while preserving ordinary public/job identifiers."""

    message = str(value)
    for secret in secrets:
        if secret:
            message = message.replace(secret, REDACTED)
    message = _TELEGRAM_URL.sub(r"\1[REDACTED]", message)
    message = _BEARER.sub(rf"\1{REDACTED}", message)
    return _SENSITIVE_VALUE.sub(rf"\1{REDACTED}", message)


class SecretRedactionFilter(logging.Filter):
    """Backward-compatible record filter using the centralized redactor."""

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = tuple(secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_secrets(record.getMessage(), self._secrets)
        record.args = ()
        return True


class RedactingFormatter(logging.Formatter):
    """Format one-line text logs, including safe async-flow identifiers."""

    def converter(self, timestamp: float | None) -> time.struct_time:
        return time.gmtime(timestamp)

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__(
            fmt="%(asctime)sZ %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        self._secrets = tuple(secret for secret in secrets if secret)

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        context = " ".join(
            f"{field}={getattr(record, field)}"
            for field in _CORRELATION_FIELDS
            if hasattr(record, field)
        )
        if context:
            rendered = f"{rendered} {context}"
        rendered = rendered.replace("\r", "\\r").replace("\n", "\\n")
        return redact_secrets(rendered, self._secrets)


def configure_logging(settings: Settings) -> None:
    """Replace root handlers with one stdout/stderr-compatible stream handler."""

    handler = logging.StreamHandler()
    handler.setFormatter(
        RedactingFormatter(
            (
                settings.bot_token.get_secret_value(),
                settings.internal_api_token.get_secret_value(),
            )
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.app_log_level)
