"""Stage 30.6.7 final production-surface closure regressions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.enums import (
    DownloadPlanOperation,
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    ProviderRuntimeStatus,
    QualityProfile,
)
from app.core.exceptions import UnsupportedMediaType, UnsupportedProvider
from app.core.models import DownloadProviderCandidate, NativeMediaInfo
from app.core.provider_accounts import ProviderAuthorizationMethod
from app.core.quality import plans_for_candidate
from app.provider_integration import (
    DEFAULT_PROVIDER_INTEGRATIONS,
    ProviderAccountIntegrationMode,
    ProviderSearchIntegrationState,
)
from app.providers.base import AlbumReference, PlaylistReference, TrackReference
from app.providers.onthespot.capabilities import ONTHESPOT_CAPABILITIES
from app.providers.onthespot.provider import OnTheSpotProvider

_PROFILES = (
    QualityProfile.AAC_128,
    QualityProfile.AAC_256,
    QualityProfile.MP3_128,
    QualityProfile.MP3_320,
    QualityProfile.LOSSLESS,
)


def _candidate(provider: MusicProviderName, native: NativeMediaInfo) -> DownloadProviderCandidate:
    return DownloadProviderCandidate(
        track_id=3067,
        track_source_id=3067,
        provider=provider,
        provider_track_id=f"{provider.value}-track",
        runtime_status=ProviderRuntimeStatus.AVAILABLE,
        capabilities=ONTHESPOT_CAPABILITIES[provider],
        native_media_info=native,
    )


def test_final_stage306_provider_registry_keeps_public_and_managed_boundaries_exact() -> None:
    registry = DEFAULT_PROVIDER_INTEGRATIONS

    for provider in (
        MusicProviderName.YOUTUBE_MUSIC,
        MusicProviderName.BANDCAMP,
        MusicProviderName.SOUNDCLOUD,
    ):
        spec = registry.for_provider(provider)
        assert spec.search is ProviderSearchIntegrationState.ENABLED
        assert spec.account is ProviderAccountIntegrationMode.NOT_REQUIRED
        assert spec.authorization_methods == ()

    for provider, method in (
        (MusicProviderName.QOBUZ, ProviderAuthorizationMethod.QOBUZ_CREDENTIALS),
        (MusicProviderName.APPLE_MUSIC, ProviderAuthorizationMethod.APPLE_MUSIC_SESSION_TOKEN),
    ):
        spec = registry.for_provider(provider)
        assert spec.search is ProviderSearchIntegrationState.ENABLED
        assert spec.account is ProviderAccountIntegrationMode.MANAGED
        assert spec.authorization_methods == (method,)


@pytest.mark.parametrize(
    ("provider", "native", "native_profile"),
    [
        (
            MusicProviderName.YOUTUBE_MUSIC,
            NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 128),
            QualityProfile.AAC_128,
        ),
        (
            MusicProviderName.QOBUZ,
            NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
            QualityProfile.LOSSLESS,
        ),
        (
            MusicProviderName.APPLE_MUSIC,
            NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 256),
            QualityProfile.AAC_256,
        ),
        (
            MusicProviderName.BANDCAMP,
            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 128),
            QualityProfile.MP3_128,
        ),
        (
            MusicProviderName.SOUNDCLOUD,
            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 128),
            QualityProfile.MP3_128,
        ),
    ],
)
def test_final_native_quality_matrix_has_one_exact_direct_profile(
    provider: MusicProviderName,
    native: NativeMediaInfo,
    native_profile: QualityProfile,
) -> None:
    candidate = _candidate(provider, native)

    for profile in _PROFILES:
        direct = tuple(
            plan
            for plan in plans_for_candidate(candidate, profile)
            if plan.operation is DownloadPlanOperation.DIRECT
        )
        assert bool(direct) is (profile is native_profile)

    # Qobuz's established lossless-to-lossy transcode policy remains distinct
    # from its one native profile. Every other final provider is exact-only.
    if provider is MusicProviderName.QOBUZ:
        assert all(
            plans_for_candidate(candidate, profile)[0].operation is DownloadPlanOperation.TRANSCODE
            for profile in _PROFILES
            if profile is not QualityProfile.LOSSLESS
        )
    else:
        assert all(
            plans_for_candidate(candidate, profile) == ()
            for profile in _PROFILES
            if profile is not native_profile
        )


@pytest.mark.parametrize(
    ("url", "reference_type", "provider"),
    [
        (
            "https://music.apple.com/us/album/release/album-1?i=456",
            TrackReference,
            MusicProviderName.APPLE_MUSIC,
        ),
        (
            "https://music.apple.com/us/album/release/album-1",
            AlbumReference,
            MusicProviderName.APPLE_MUSIC,
        ),
        (
            "https://music.apple.com/us/playlist/list-1",
            PlaylistReference,
            MusicProviderName.APPLE_MUSIC,
        ),
        ("https://play.qobuz.com/track/track-1", TrackReference, MusicProviderName.QOBUZ),
        ("https://play.qobuz.com/album/album-1", AlbumReference, MusicProviderName.QOBUZ),
        (
            "https://play.qobuz.com/playlist/list-1",
            PlaylistReference,
            MusicProviderName.QOBUZ,
        ),
        (
            "https://music.youtube.com/watch?v=abc_123-XYZ",
            TrackReference,
            MusicProviderName.YOUTUBE_MUSIC,
        ),
        (
            "https://music.youtube.com/playlist?list=PLabc_1234567890",
            PlaylistReference,
            MusicProviderName.YOUTUBE_MUSIC,
        ),
        (
            "https://artist.bandcamp.com/track/a-release",
            TrackReference,
            MusicProviderName.BANDCAMP,
        ),
        (
            "https://artist.bandcamp.com/album/a-release",
            AlbumReference,
            MusicProviderName.BANDCAMP,
        ),
        (
            "https://soundcloud.com/artist/a-public-track",
            TrackReference,
            MusicProviderName.SOUNDCLOUD,
        ),
    ],
)
def test_final_url_matrix_preserves_only_explicit_track_and_collection_forms(
    url: str, reference_type: type[object], provider: MusicProviderName
) -> None:
    reference = OnTheSpotProvider.__new__(OnTheSpotProvider).detect_media(url)

    assert isinstance(reference, reference_type)
    assert reference.provider is provider


@pytest.mark.parametrize(
    "url",
    [
        "https://music.youtube.com/playlist?list=RDCLAK5uy_dynamic",
        "https://music.youtube.com/playlist?list=OLAK5uy_album_form",
        "https://music.youtube.com/playlist?list=LLliked_library",
        "https://www.youtube.com/playlist?list=PLabc_1234567890",
        "https://artist.bandcamp.com/playlist/release",
        "https://soundcloud.com/artist/sets/a-set",
        "https://soundcloud.com/artist/likes",
        "https://soundcloud.com/artist",
    ],
)
def test_final_deferred_url_matrix_is_not_routable(url: str) -> None:
    with pytest.raises((UnsupportedMediaType, UnsupportedProvider)):
        OnTheSpotProvider.__new__(OnTheSpotProvider).detect_media(url)


@pytest.mark.asyncio
async def test_soundcloud_collection_and_oauth_like_state_cannot_enter_production_paths() -> None:
    process = SimpleNamespace(
        match_url=AsyncMock(
            return_value={"service": "soundcloud", "item_type": "playlist", "item_id": "set-1"}
        ),
        list_provider_accounts=AsyncMock(return_value=("oauth-account-id",)),
    )
    provider = OnTheSpotProvider(process)  # type: ignore[arg-type]

    with pytest.raises(UnsupportedMediaType):
        await provider.classify_url("https://soundcloud.com/artist/sets/a-set")
    assert await provider.list_provider_accounts(MusicProviderName.SOUNDCLOUD) == ()
    process.list_provider_accounts.assert_not_awaited()
