"""Compact, ownership-neutral callback codec for Stage 22 settings."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.core.enums import DeliveryMode, FormatPreference, QualityPreference


class PreferenceSetting(StrEnum):
    QUALITY = "q"
    FORMAT = "f"
    DELIVERY = "d"
    METADATA = "m"
    COVER = "c"


@dataclass(frozen=True, slots=True)
class PreferenceCallback:
    setting: PreferenceSetting
    value: str


def encode_preference_callback(setting: PreferenceSetting, value: str) -> str:
    if not value or ":" in value:
        raise ValueError("invalid preference callback value")
    result = f"dp1:{setting.value}:{value}"
    if len(result.encode()) > 64:
        raise ValueError("callback exceeds Telegram limit")
    return result


def parse_preference_callback(value: str | None) -> PreferenceCallback | None:
    if not value:
        return None
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != "dp1" or not parts[2]:
        return None
    try:
        setting = PreferenceSetting(parts[1])
    except ValueError:
        return None
    if setting is PreferenceSetting.QUALITY:
        try:
            QualityPreference(parts[2])
        except ValueError:
            return None
    elif setting is PreferenceSetting.FORMAT:
        try:
            FormatPreference(parts[2])
        except ValueError:
            return None
    elif setting is PreferenceSetting.DELIVERY:
        try:
            DeliveryMode(parts[2])
        except ValueError:
            return None
    elif parts[2] not in {"on", "off"}:
        return None
    return PreferenceCallback(setting, parts[2])
