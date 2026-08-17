from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def make_settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_worker_defaults_and_limits_are_valid() -> None:
    settings = make_settings()
    assert settings.download_workers_default == 2
    assert settings.download_workers_max == 8
    assert settings.upload_workers_default == 3
    assert settings.upload_workers_max == 10


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


def test_supported_locales_are_parsed_from_comma_separated_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPPORTED_LOCALES", "en, ru, de")
    settings = make_settings()
    assert settings.supported_locales == ("en", "ru", "de")
