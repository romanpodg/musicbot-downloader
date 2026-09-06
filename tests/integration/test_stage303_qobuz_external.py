"""Optional credentialed Qobuz smoke; never part of normal CI."""

from __future__ import annotations

import os

import pytest

from app.core.enums import MusicProviderName
from app.core.models import TrackSearchRequest
from app.core.provider_accounts import SensitiveValue
from app.providers.onthespot.provider import OnTheSpotProvider


@pytest.mark.external
@pytest.mark.asyncio
async def test_qobuz_authenticated_search_metadata_and_source_smoke() -> None:
    if not os.environ.get("QOBUZ_EMAIL") or not os.environ.get("QOBUZ_PASSWORD"):
        pytest.skip("QOBUZ_EXTERNAL_SMOKE=NOT_RUN (credentials not supplied)")
    provider = OnTheSpotProvider()
    try:
        auth = await provider.authorize_qobuz_credentials(
            SensitiveValue(os.environ["QOBUZ_EMAIL"]),
            SensitiveValue(os.environ["QOBUZ_PASSWORD"]),
        )
        assert auth.persisted
        results = await provider.search_tracks(
            TrackSearchRequest(
                MusicProviderName.QOBUZ, os.environ.get("QOBUZ_QUERY", "Daft Punk"), 1
            )
        )
        assert results and results[0].provider is MusicProviderName.QOBUZ
        metadata = await provider.get_track_metadata(
            MusicProviderName.QOBUZ, results[0].provider_track_id
        )
        assert metadata.provider is MusicProviderName.QOBUZ
        source = await provider.check_source(MusicProviderName.QOBUZ, results[0].provider_track_id)
        assert source.status.value in {"AVAILABLE", "SOURCE_UNAVAILABLE", "ERROR"}
    finally:
        await provider.close()
