from __future__ import annotations

import os

import pytest

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
