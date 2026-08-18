from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.exceptions import LocalizationError, LocalizationFormatError
from app.i18n.service import LocalizationService


def _write_catalogs(root: Path, catalogs: dict[str, dict[str, str]]) -> None:
    for locale, messages in catalogs.items():
        directory = root / locale
        directory.mkdir()
        (directory / "messages.json").write_text(json.dumps(messages), encoding="utf-8")


def test_explicit_preferred_locale_wins_and_normalizes() -> None:
    service = LocalizationService(("en", "ru"), "en")
    assert service.resolve_locale("  ru_RU ", "en") == "ru"


def test_supported_telegram_language_is_used_without_preference() -> None:
    service = LocalizationService(("en", "ru"), "en")
    assert service.resolve_locale(None, "ru-RU") == "ru"


def test_unsupported_or_malformed_telegram_language_falls_back() -> None:
    service = LocalizationService(("en", "ru"), "en")
    assert service.resolve_locale(None, "de-DE") == "en"
    assert service.resolve_locale("   ", " !!! ") == "en"


def test_no_locale_falls_back_to_default() -> None:
    service = LocalizationService(("en", "ru"), "en")
    assert service.resolve_locale(None, None) == "en"


def test_missing_translation_uses_default_then_key(tmp_path: Path) -> None:
    _write_catalogs(tmp_path, {"en": {"only.default": "Default"}, "ru": {}})
    service = LocalizationService(("en", "ru"), "en", tmp_path, validate_catalog_parity=False)
    assert service.translate("only.default", "ru") == "Default"
    assert service.translate("missing.everywhere", "ru") == "missing.everywhere"


def test_catalog_parity_is_validated(tmp_path: Path) -> None:
    _write_catalogs(tmp_path, {"en": {"required": "Yes"}, "ru": {}})
    with pytest.raises(LocalizationError):
        LocalizationService(("en", "ru"), "en", tmp_path)


def test_catalog_placeholder_parity_is_validated(tmp_path: Path) -> None:
    _write_catalogs(
        tmp_path,
        {"en": {"hello": "Hello {name}"}, "ru": {"hello": "Привет {user}"}},
    )
    with pytest.raises(LocalizationError):
        LocalizationService(("en", "ru"), "en", tmp_path)


def test_default_locale_must_be_supported() -> None:
    with pytest.raises(LocalizationError):
        LocalizationService(("en", "ru"), "de")


def test_interpolation_errors_are_typed(tmp_path: Path) -> None:
    _write_catalogs(tmp_path, {"en": {"hello": "Hello {name}"}})
    service = LocalizationService(("en",), "en", tmp_path)
    with pytest.raises(LocalizationFormatError):
        service.translate("hello", "en")
