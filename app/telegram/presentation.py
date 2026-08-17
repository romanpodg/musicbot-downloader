"""Localized Stage 9 keyboards and compact callback codecs."""

from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.enums import QualityProfile
from app.i18n import LocalizationService

QUALITY_CODES = {
    QualityProfile.MP3_128: "1",
    QualityProfile.MP3_320: "2",
    QualityProfile.AAC_256: "3",
    QualityProfile.LOSSLESS: "4",
}
CODE_QUALITIES = {value: key for key, value in QUALITY_CODES.items()}


@dataclass(frozen=True, slots=True)
class FirstQualityCallback:
    request_id: int
    quality_profile: QualityProfile


def encode_first_quality(request_id: int, quality: QualityProfile) -> str:
    if request_id <= 0:
        raise ValueError("invalid request ID")
    return f"q1:{request_id}:{QUALITY_CODES[quality]}"


def parse_first_quality(value: str | None) -> FirstQualityCallback | None:
    if not value:
        return None
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != "q1" or parts[2] not in CODE_QUALITIES:
        return None
    try:
        request_id = int(parts[1])
    except ValueError:
        return None
    if request_id <= 0:
        return None
    return FirstQualityCallback(request_id, CODE_QUALITIES[parts[2]])


def encode_setting_quality(quality: QualityProfile) -> str:
    return f"sq1:{QUALITY_CODES[quality]}"


def parse_setting_quality(value: str | None) -> QualityProfile | None:
    if not value:
        return None
    parts = value.split(":")
    return CODE_QUALITIES.get(parts[1]) if len(parts) == 2 and parts[0] == "sq1" else None


def encode_locale(locale: str) -> str:
    return f"l1:{locale}"


def parse_locale(value: str | None) -> str | None:
    if not value:
        return None
    parts = value.split(":")
    return parts[1] if len(parts) == 2 and parts[0] == "l1" and parts[1] in {"en", "ru"} else None


class TelegramPresentation:
    def __init__(self, i18n: LocalizationService) -> None:
        self.i18n = i18n

    def text(self, key: str, locale: str, **values: object) -> str:
        return self.i18n.translate(key, locale, **values)

    def quality_keyboard(
        self, locale: str, *, request_id: int | None = None
    ) -> InlineKeyboardMarkup:
        rows = []
        for quality in QualityProfile:
            callback = (
                encode_first_quality(request_id, quality)
                if request_id is not None
                else encode_setting_quality(quality)
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text=self.text(f"quality.{quality.value}.label", locale),
                        callback_data=callback,
                    )
                ]
            )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def language_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="English", callback_data=encode_locale("en"))],
                [InlineKeyboardButton(text="Русский", callback_data=encode_locale("ru"))],
            ]
        )

    def quality_name(self, quality: QualityProfile | None, locale: str) -> str:
        if quality is None:
            return self.text("quality.not_selected", locale)
        return self.text(f"quality.{quality.value}.label", locale)
