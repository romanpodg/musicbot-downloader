"""JSON-resource localization without presentation-framework coupling."""

from __future__ import annotations

import json
from pathlib import Path
from string import Formatter
from typing import Any

from app.core.exceptions import LocalizationError, LocalizationFormatError


class LocalizationService:
    def __init__(
        self,
        supported_locales: tuple[str, ...],
        default_locale: str,
        locales_dir: Path | None = None,
        *,
        validate_catalog_parity: bool = True,
    ) -> None:
        self.supported_locales = tuple(self._normalize(locale) for locale in supported_locales)
        self.default_locale = self._normalize(default_locale)
        if not self.supported_locales or self.default_locale not in self.supported_locales:
            raise LocalizationError()
        self.locales_dir = locales_dir or Path(__file__).parent / "locales"
        self._catalogs = {locale: self._load_catalog(locale) for locale in self.supported_locales}
        if validate_catalog_parity:
            self._validate_catalog_parity()

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
        try:
            return text.format_map(values)
        except (KeyError, ValueError, IndexError) as exc:
            raise LocalizationFormatError() from exc

    def _normalize_supported(self, locale: str | None) -> str | None:
        if not locale or not locale.strip():
            return None
        normalized = self._normalize(locale)
        if normalized in self.supported_locales:
            return normalized
        language = normalized.split("-", maxsplit=1)[0]
        return language if language in self.supported_locales else None

    def _load_catalog(self, locale: str) -> dict[str, str]:
        path = self.locales_dir / locale / "messages.json"
        try:
            with path.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise LocalizationError() from exc
        if not isinstance(data, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in data.items()
        ):
            raise LocalizationError()
        return data

    def _validate_catalog_parity(self) -> None:
        baseline_catalog = self._catalogs[self.default_locale]
        baseline = set(baseline_catalog)
        if any(set(catalog) != baseline for catalog in self._catalogs.values()):
            raise LocalizationError()
        try:
            baseline_fields = {
                key: _format_fields(value) for key, value in baseline_catalog.items()
            }
            if any(
                _format_fields(catalog[key]) != baseline_fields[key]
                for catalog in self._catalogs.values()
                for key in baseline
            ):
                raise LocalizationError()
        except ValueError as exc:
            raise LocalizationError() from exc

    @staticmethod
    def _normalize(locale: str) -> str:
        return locale.strip().lower().replace("_", "-")


def _format_fields(value: str) -> frozenset[str]:
    return frozenset(
        field_name for _, field_name, _, _ in Formatter().parse(value) if field_name is not None
    )
