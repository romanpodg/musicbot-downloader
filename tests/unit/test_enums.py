from app.core.enums import QualityProfile


def test_quality_profiles_are_exactly_the_delivery_contract() -> None:
    assert {profile.name for profile in QualityProfile} == {
        "MP3_128",
        "MP3_320",
        "AAC_256",
        "LOSSLESS",
    }
