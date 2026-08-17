"""Application logging setup."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from app.config import Settings


class SecretRedactionFilter(logging.Filter):
    """Redact configured secrets if a dependency includes one in a log message."""

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for secret in self._secrets:
            message = message.replace(secret, "[REDACTED]")
        record.msg = message
        record.args = ()
        return True


def configure_logging(settings: Settings) -> None:
    """Configure structured-enough text logging for the service process."""

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    handler.addFilter(
        SecretRedactionFilter(
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
