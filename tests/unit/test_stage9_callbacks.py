from __future__ import annotations

import pytest

from app.core.enums import QualityProfile, TelegramDeliveryStatus
from app.i18n import LocalizationService
from app.services.telegram_requests import TrackCard
from app.telegram.presentation import (
    TelegramPresentation,
    encode_first_quality,
    encode_locale,
    encode_other_quality,
    encode_setting_quality,
    encode_track_back,
    encode_track_download,
    encode_track_quality,
    parse_first_quality,
    parse_locale,
    parse_other_quality,
    parse_setting_quality,
    parse_track_back,
    parse_track_download,
    parse_track_quality,
)


@pytest.mark.parametrize("quality", list(QualityProfile))
def test_compact_quality_callbacks_round_trip(quality: QualityProfile) -> None:
    encoded = encode_first_quality(42, quality)
    parsed = parse_first_quality(encoded)
    assert parsed is not None
    assert parsed.request_id == 42
    assert parsed.quality_profile is quality
    assert len(encoded.encode()) < 64
    assert parse_setting_quality(encode_setting_quality(quality)) is quality


@pytest.mark.parametrize("value", [None, "", "q1", "q1:x:1", "q1:0:1", "q1:1:5", "{}"])
def test_malformed_quality_callbacks_are_rejected(value: str | None) -> None:
    assert parse_first_quality(value) is None


def test_language_callbacks_are_bounded() -> None:
    assert parse_locale(encode_locale("en")) == "en"
    assert parse_locale(encode_locale("ru")) == "ru"
    assert parse_locale("l1:de") is None


@pytest.mark.parametrize("quality", list(QualityProfile))
def test_track_card_callbacks_are_versioned_compact_and_round_trip(
    quality: QualityProfile,
) -> None:
    request_id = 987654321
    values = (
        encode_track_download(request_id),
        encode_other_quality(request_id),
        encode_track_quality(request_id, quality),
        encode_track_back(request_id),
    )
    assert all(len(value.encode()) < 64 for value in values)
    assert parse_track_download(values[0]) == request_id
    assert parse_other_quality(values[1]) == request_id
    parsed = parse_track_quality(values[2])
    assert parsed is not None
    assert parsed.request_id == request_id
    assert parsed.quality_profile is quality
    assert parse_track_back(values[3]) == request_id


@pytest.mark.parametrize("value", [None, "", "td1", "td1:x", "td1:0", "td1:1:x", "{}"])
def test_malformed_track_download_callbacks_are_rejected(value: str | None) -> None:
    assert parse_track_download(value) is None


def test_track_card_plain_text_metadata_duration_buttons_and_bounding() -> None:
    presentation = TelegramPresentation(LocalizationService(("en", "ru"), "en"))
    card = TrackCard(
        request_id=9,
        status=TelegramDeliveryStatus.AWAITING_ACTION,
        quality_profile=QualityProfile.MP3_320,
        artist="Артист & <artist> *_[] 日本語",
        title="Title & <title> *_[] 🎵" + "x" * 5000,
        album="Album & <album> *_[]",
        duration_ms=3_661_999,
        card_message_id=None,
    )
    text = presentation.track_card_text(card, "en")
    keyboard = presentation.track_card_keyboard(
        "en", request_id=card.request_id, quality=QualityProfile.MP3_320
    )
    assert text.startswith("Артист & <artist> *_[] 日本語 — Title & <title> *_[] 🎵")
    assert len(text) == 4096
    assert text.endswith("…")
    assert len(keyboard.inline_keyboard) == 2
    assert keyboard.inline_keyboard[0][0].text == "Download · MP3 320"
    assert keyboard.inline_keyboard[1][0].text == "Other quality"


def test_track_quality_picker_has_exactly_four_profiles_and_back() -> None:
    presentation = TelegramPresentation(LocalizationService(("en", "ru"), "en"))
    keyboard = presentation.track_quality_keyboard("en", request_id=12)
    quality_buttons = [button for row in keyboard.inline_keyboard[:2] for button in row]
    assert [button.text for button in quality_buttons] == [
        "MP3 128",
        "MP3 320",
        "AAC 256",
        "Lossless",
    ]
    assert keyboard.inline_keyboard[2][0].text == "Back"


def test_track_card_omits_missing_optional_metadata_and_formats_short_duration() -> None:
    presentation = TelegramPresentation(LocalizationService(("en", "ru"), "en"))
    card = TrackCard(
        request_id=13,
        status=TelegramDeliveryStatus.AWAITING_ACTION,
        quality_profile=QualityProfile.AAC_256,
        artist="宇多田ヒカル feat. 누구",
        title="Пример 🎧",
        album=None,
        duration_ms=238_999,
        card_message_id=None,
    )
    text = presentation.track_card_text(card, "en")
    assert "宇多田ヒカル feat. 누구 — Пример 🎧" in text
    assert "Duration: 3:58" in text
    assert "Album:" not in text
    assert "unknown" not in text.lower()
