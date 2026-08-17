from __future__ import annotations

import json
from pathlib import Path

from app.i18n.service import LocalizationService


def test_explicit_preferred_locale_wins() -> None:
    service = LocalizationService(("en", "ru"), "en")
    assert service.resolve_locale("ru", "en") == "ru"


def test_supported_telegram_language_is_used_without_preference() -> None:
    service = LocalizationService(("en", "ru"), "en")
    assert service.resolve_locale(None, "ru-RU") == "ru"


def test_unsupported_telegram_language_falls_back_to_default() -> None:
    service = LocalizationService(("en", "ru"), "en")
    assert service.resolve_locale(None, "de-DE") == "en"


def test_no_locale_falls_back_to_default() -> None:
    service = LocalizationService(("en", "ru"), "en")
    assert service.resolve_locale(None, None) == "en"


def test_missing_translation_uses_default_then_key(tmp_path: Path) -> None:
    for locale, messages in (("en", {"only.default": "Default"}), ("ru", {})):
        directory = tmp_path / locale
        directory.mkdir()
        (directory / "messages.json").write_text(json.dumps(messages), encoding="utf-8")

    service = LocalizationService(("en", "ru"), "en", tmp_path)
    assert service.translate("only.default", "ru") == "Default"
    assert service.translate("missing.everywhere", "ru") == "missing.everywhere"
