from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.core.exceptions import ConfigurationError


def make_settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_worker_defaults_and_limits_are_valid() -> None:
    settings = make_settings()
    assert settings.download_workers_default == 2
    assert settings.download_workers_max == 8
    assert settings.upload_workers_default == 3
    assert settings.upload_workers_max == 10
    assert settings.temp_disk_min_free_bytes == 268_435_456


@pytest.mark.parametrize(
    ("values", "field"),
    [
        ({"download_workers_default": 0}, "download_workers_default"),
        ({"download_workers_default": 3, "download_workers_max": 2}, "DOWNLOAD_WORKERS_MAX"),
        ({"upload_workers_default": 0}, "upload_workers_default"),
        ({"upload_workers_default": 4, "upload_workers_max": 3}, "UPLOAD_WORKERS_MAX"),
        ({"queue_max_size": 0}, "queue_max_size"),
    ],
)
def test_invalid_worker_and_queue_limits_are_rejected(
    values: dict[str, object], field: str
) -> None:
    with pytest.raises(ValidationError, match=field):
        make_settings(**values)


def test_default_locale_must_be_supported() -> None:
    with pytest.raises(ValidationError, match="DEFAULT_LOCALE"):
        make_settings(default_locale="de", supported_locales="en,ru")


def test_supported_locales_are_parsed_and_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPPORTED_LOCALES", " en, ru_RU , de-DE ")
    settings = make_settings()
    assert settings.supported_locales == ("en", "ru-ru", "de-de")


@pytest.mark.parametrize("owner_id", [0, -1, "not-an-id"])
def test_owner_id_must_be_a_positive_integer(owner_id: object) -> None:
    with pytest.raises(ValidationError):
        make_settings(owner_id=owner_id)


def test_empty_owner_id_remains_optional() -> None:
    assert make_settings(owner_id="").owner_id is None


@pytest.mark.parametrize("level", ["TRACE", "WARN", "20", ""])
def test_invalid_application_log_level_is_rejected(level: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(app_log_level=level)


def test_application_log_level_is_normalized() -> None:
    assert make_settings(app_log_level=" warning ").app_log_level == "WARNING"


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:///./data/test.db",
        "sqlite+pysqlite:///./data/test.db",
        "not a database url",
    ],
)
def test_sqlite_database_url_requires_async_driver(database_url: str) -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        make_settings(database_url=database_url)


def test_parent_application_log_level_does_not_require_generic_log_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.setenv("APP_LOG_LEVEL", "INFO")
    assert make_settings().app_log_level == "INFO"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("-100123456", -100123456), ("@private_cache", "@private_cache"), (12345, 12345)],
)
def test_telegram_cache_chat_id_supports_numeric_and_named_destinations(
    raw: object, expected: int | str
) -> None:
    settings = make_settings(
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789",
        telegram_cache_chat_id=raw,
    )
    _, chat_id = settings.telegram_cache_configuration()
    assert chat_id == expected


def test_telegram_configuration_is_lazy_but_strict_when_requested() -> None:
    settings = make_settings()
    with pytest.raises(ConfigurationError):
        settings.telegram_cache_configuration()


def test_internal_api_is_disabled_and_loopback_bound_by_default() -> None:
    settings = make_settings()
    assert settings.internal_api_configuration() is None
    assert settings.internal_api_host == "127.0.0.1"
    assert settings.internal_api_port == 8081


@pytest.mark.parametrize("token", ["", "short", " x" * 20])
def test_enabled_internal_api_requires_a_strong_trimmed_token(token: str) -> None:
    with pytest.raises(ValidationError, match="INTERNAL_API_TOKEN"):
        make_settings(internal_api_enabled=True, internal_api_token=token)


def test_enabled_internal_api_returns_validated_listener_configuration() -> None:
    settings = make_settings(
        internal_api_enabled=True,
        internal_api_host=" localhost ",
        internal_api_port=9091,
        internal_api_token="x" * 32,
    )
    assert settings.internal_api_configuration() == ("localhost", 9091, "x" * 32)


@pytest.mark.parametrize("port", [0, 65536])
def test_internal_api_port_is_bounded(port: int) -> None:
    with pytest.raises(ValidationError):
        make_settings(internal_api_port=port)


def test_temp_disk_reserve_must_not_be_negative() -> None:
    with pytest.raises(ValidationError):
        make_settings(temp_disk_min_free_bytes=-1)
