from __future__ import annotations

import os

import pytest

from app.core.enums import MusicProviderName, ProviderRuntimeStatus
from app.core.models import TrackSearchRequest
from app.providers.onthespot import OnTheSpotProvider


@pytest.mark.external
@pytest.mark.asyncio
async def test_real_onthespot_metadata_when_explicitly_enabled() -> None:
    url = os.getenv("ONTHESPOT_TEST_TRACK_URL")
    if not url:
        pytest.skip("Set ONTHESPOT_TEST_TRACK_URL and configure OnTheSpot authentication")
    provider = OnTheSpotProvider()
    try:
        expected_url = provider.detect_url(url).source_url
        metadata = await provider.get_metadata(url)
        assert metadata.provider_track_id
        assert metadata.source_url == expected_url
    finally:
        await provider.close()


@pytest.mark.external
@pytest.mark.asyncio
async def test_real_onthespot_search_when_explicitly_enabled() -> None:
    provider_name = os.getenv("ONTHESPOT_TEST_SEARCH_PROVIDER")
    query = os.getenv("ONTHESPOT_TEST_SEARCH_QUERY")
    if not provider_name or not query:
        pytest.skip("Set ONTHESPOT_TEST_SEARCH_PROVIDER and ONTHESPOT_TEST_SEARCH_QUERY")
    provider = OnTheSpotProvider()
    try:
        target = MusicProviderName(provider_name)
        searchable = await provider.list_searchable_providers()
        if target not in searchable:
            pytest.skip(f"Provider account is not searchable: {target.value}")
        candidates = await provider.search_tracks(TrackSearchRequest(target, query, 3))
        assert len(candidates) <= 3
        assert all(candidate.provider is target for candidate in candidates)
        if candidates:
            metadata = await provider.get_metadata(candidates[0].url)
            assert metadata.provider is target
    finally:
        await provider.close()


@pytest.mark.external
@pytest.mark.asyncio
async def test_public_source_readiness_when_explicitly_enabled() -> None:
    url = os.getenv("ONTHESPOT_TEST_PUBLIC_TRACK_URL")
    if not url:
        pytest.skip("Set ONTHESPOT_TEST_PUBLIC_TRACK_URL to a Bandcamp or YouTube Music track")
    provider = OnTheSpotProvider()
    try:
        reference = provider.detect_url(url)
        if reference.provider not in {
            MusicProviderName.BANDCAMP,
            MusicProviderName.YOUTUBE_MUSIC,
        }:
            pytest.skip("ONTHESPOT_TEST_PUBLIC_TRACK_URL must use a tokenless provider")
        metadata = await provider.get_metadata(url)
        check = await provider.check_source(metadata.provider, metadata.provider_track_id)
        assert check.status is ProviderRuntimeStatus.AVAILABLE
    finally:
        await provider.close()
