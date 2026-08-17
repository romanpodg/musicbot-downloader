"""JSON-resource localization without presentation-framework coupling."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class LocalizationService:
    def __init__(
        self,
        supported_locales: tuple[str, ...],
        default_locale: str,
        locales_dir: Path | None = None,
    ) -> None:
        self.supported_locales = tuple(locale.lower() for locale in supported_locales)
        self.default_locale = default_locale.lower()
        self.locales_dir = locales_dir or Path(__file__).parent / "locales"
        self._catalogs = {locale: self._load_catalog(locale) for locale in self.supported_locales}

    def resolve_locale(
        self,
        preferred_locale: str | None,
        telegram_language_code: str | None,
    ) -> str:
        for candidate in (preferred_locale, telegram_language_code):
            normalized = self._normalize_supported(candidate)
            if normalized is not None:
                return normalized
        return self.default_locale

    def translate(self, key: str, locale: str | None = None, **values: Any) -> str:
        resolved = self._normalize_supported(locale) or self.default_locale
        text = self._catalogs.get(resolved, {}).get(key)
        if text is None and resolved != self.default_locale:
            text = self._catalogs.get(self.default_locale, {}).get(key)
        if text is None:
            return key
        return text.format_map(values)

    def _normalize_supported(self, locale: str | None) -> str | None:
        if not locale:
            return None
        normalized = locale.lower().replace("_", "-")
        if normalized in self.supported_locales:
            return normalized
        language = normalized.split("-", maxsplit=1)[0]
        return language if language in self.supported_locales else None

    def _load_catalog(self, locale: str) -> dict[str, str]:
        path = self.locales_dir / locale / "messages.json"
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in data.items()
        ):
            raise ValueError(f"Invalid locale catalog: {path}")
        return data
