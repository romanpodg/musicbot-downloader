"""Localized Track Cards, keyboards, and compact callback codecs."""

from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.enums import QualityProfile
from app.i18n import LocalizationService
from app.services.telegram_requests import TrackCard

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


@dataclass(frozen=True, slots=True)
class TrackQualityCallback:
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


def encode_track_download(request_id: int) -> str:
    return _encode_request("td1", request_id)


def parse_track_download(value: str | None) -> int | None:
    return _parse_request(value, "td1")


def encode_other_quality(request_id: int) -> str:
    return _encode_request("to1", request_id)


def parse_other_quality(value: str | None) -> int | None:
    return _parse_request(value, "to1")


def encode_track_quality(request_id: int, quality: QualityProfile) -> str:
    if request_id <= 0:
        raise ValueError("invalid request ID")
    return f"tq1:{request_id}:{QUALITY_CODES[quality]}"


def parse_track_quality(value: str | None) -> TrackQualityCallback | None:
    if not value:
        return None
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != "tq1" or parts[2] not in CODE_QUALITIES:
        return None
    request_id = _positive_int(parts[1])
    if request_id is None:
        return None
    return TrackQualityCallback(request_id, CODE_QUALITIES[parts[2]])


def encode_track_back(request_id: int) -> str:
    return _encode_request("tb1", request_id)


def parse_track_back(value: str | None) -> int | None:
    return _parse_request(value, "tb1")


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

    def track_card_keyboard(
        self, locale: str, *, request_id: int, quality: QualityProfile
    ) -> InlineKeyboardMarkup:
        quality_name = self.quality_name(quality, locale)
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=self.text("bot.download_quality_button", locale, quality=quality_name),
                        callback_data=encode_track_download(request_id),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=self.text("bot.other_quality_button", locale),
                        callback_data=encode_other_quality(request_id),
                    )
                ],
            ]
        )

    def track_quality_keyboard(self, locale: str, *, request_id: int) -> InlineKeyboardMarkup:
        choices = [
            InlineKeyboardButton(
                text=self.quality_name(quality, locale),
                callback_data=encode_track_quality(request_id, quality),
            )
            for quality in QualityProfile
        ]
        return InlineKeyboardMarkup(
            inline_keyboard=[
                choices[:2],
                choices[2:],
                [
                    InlineKeyboardButton(
                        text=self.text("bot.back_button", locale),
                        callback_data=encode_track_back(request_id),
                    )
                ],
            ]
        )

    def track_card_text(self, card: TrackCard, locale: str, *, mode: str = "card") -> str:
        artist = card.artist or self.text("bot.track_card_unknown_artist", locale)
        title = card.title or self.text("bot.track_card_unknown_title", locale)
        sections = [f"{artist} — {title}"]
        if card.album:
            sections.append(self.text("bot.track_card_album", locale, album=card.album))
        if card.duration_ms is not None:
            sections.append(
                self.text(
                    "bot.track_card_duration",
                    locale,
                    duration=_format_duration(card.duration_ms),
                )
            )
        if mode == "first_quality":
            sections.append(self.text("bot.choose_default_quality", locale))
        elif mode == "track_quality":
            sections.append(self.text("bot.choose_track_quality", locale))
            sections.append(self.text("bot.track_quality_one_off_hint", locale))
        elif card.quality_profile is not None:
            sections.append(
                self.text(
                    "bot.track_card_default_quality",
                    locale,
                    quality=self.quality_name(card.quality_profile, locale),
                )
            )
        return _bounded("\n\n".join(sections), 4096)

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


def _encode_request(prefix: str, request_id: int) -> str:
    if request_id <= 0:
        raise ValueError("invalid request ID")
    return f"{prefix}:{request_id}"


def _parse_request(value: str | None, prefix: str) -> int | None:
    if not value:
        return None
    parts = value.split(":")
    if len(parts) != 2 or parts[0] != prefix:
        return None
    return _positive_int(parts[1])


def _positive_int(value: str) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _format_duration(duration_ms: int) -> str:
    total_seconds = max(duration_ms, 0) // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _bounded(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"
