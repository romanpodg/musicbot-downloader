"""Central, strongly typed application configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import make_url
from sqlalchemy.exc import ArgumentError

from app.core.exceptions import ConfigurationError


def _normalize_locale(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def _parse_locales(value: Any) -> Any:
    if isinstance(value, str):
        return tuple(_normalize_locale(part) for part in value.split(",") if part.strip())
    return value


def _empty_to_none(value: Any) -> Any:
    return None if value == "" else value


def _normalize_log_level(value: Any) -> Any:
    return value.strip().upper() if isinstance(value, str) else value


LocaleTuple = Annotated[tuple[str, ...], NoDecode, BeforeValidator(_parse_locales)]
OptionalOwnerId = Annotated[int | None, BeforeValidator(_empty_to_none)]
AppLogLevel = Annotated[
    Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    BeforeValidator(_normalize_log_level),
]


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables and ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    bot_token: SecretStr = SecretStr("")
    owner_id: OptionalOwnerId = None

    database_url: str = "sqlite+aiosqlite:///./data/bot.db"
    temp_dir: Path = Path("./temp")

    download_workers_default: int = Field(default=2, ge=1)
    download_workers_max: int = Field(default=8, ge=1)
    upload_workers_default: int = Field(default=3, ge=1)
    upload_workers_max: int = Field(default=10, ge=1)
    queue_max_size: int = Field(default=1000, ge=1)

    default_locale: str = "en"
    supported_locales: LocaleTuple = ("en", "ru")

    internal_api_enabled: bool = True
    internal_api_host: str = "0.0.0.0"
    internal_api_port: int = Field(default=8080, ge=1, le=65535)
    internal_api_token: SecretStr = SecretStr("")

    app_log_level: AppLogLevel = "INFO"

    @field_validator("owner_id")
    @classmethod
    def validate_owner_id(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("OWNER_ID must be a positive integer")
        return value

    @model_validator(mode="after")
    def validate_cross_field_constraints(self) -> Settings:
        if self.download_workers_max < self.download_workers_default:
            raise ValueError("DOWNLOAD_WORKERS_MAX must be >= DOWNLOAD_WORKERS_DEFAULT")
        if self.upload_workers_max < self.upload_workers_default:
            raise ValueError("UPLOAD_WORKERS_MAX must be >= UPLOAD_WORKERS_DEFAULT")

        self.default_locale = _normalize_locale(self.default_locale)
        self.supported_locales = tuple(_normalize_locale(item) for item in self.supported_locales)
        if not self.supported_locales or any(not item for item in self.supported_locales):
            raise ValueError("SUPPORTED_LOCALES must contain at least one locale")
        if len(set(self.supported_locales)) != len(self.supported_locales):
            raise ValueError("SUPPORTED_LOCALES must not contain duplicates")
        if self.default_locale not in self.supported_locales:
            raise ValueError("DEFAULT_LOCALE must be present in SUPPORTED_LOCALES")

        try:
            database_url = make_url(self.database_url)
        except ArgumentError as exc:
            raise ValueError("DATABASE_URL must be a valid SQLAlchemy URL") from exc
        if (
            database_url.get_backend_name() == "sqlite"
            and database_url.drivername != "sqlite+aiosqlite"
        ):
            raise ValueError("SQLite DATABASE_URL must use the aiosqlite async driver")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached process settings with an application-level error contract."""

    try:
        return Settings()
    except ValueError as exc:
        raise ConfigurationError() from exc
