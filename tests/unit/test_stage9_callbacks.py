from __future__ import annotations

import pytest

from app.core.enums import QualityProfile
from app.telegram.presentation import (
    encode_first_quality,
    encode_locale,
    encode_setting_quality,
    parse_first_quality,
    parse_locale,
    parse_setting_quality,
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
