from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

from app.core.enums import MusicProviderName, NativeCodec, NativeContainer, ProviderRuntimeStatus
from app.core.models import TrackSearchRequest
from app.providers.onthespot import OnTheSpotProvider
from app.providers.onthespot.process import OnTheSpotProcessClient
from app.services.media import MediaProbe


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


@pytest.mark.external
@pytest.mark.asyncio
async def test_youtube_music_native_aac128_smoke_when_explicitly_enabled() -> None:
    """Opt-in public YTM search/metadata/source/native-download/ffprobe smoke."""

    url = os.getenv("ONTHESPOT_YTM_TEST_TRACK_URL")
    if not url:
        pytest.skip("Set ONTHESPOT_YTM_TEST_TRACK_URL for the YTM smoke")
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe is required for the YTM smoke")
    root = Path(tempfile.mkdtemp(prefix="musicbot-ytm-smoke-"))
    client = OnTheSpotProcessClient(temp_dir=root)
    provider = OnTheSpotProvider(client)
    job_id = uuid.uuid4().hex
    source_dir = root / job_id / "attempt-001" / "source"
    source_dir.mkdir(parents=True)
    try:
        reference = provider.detect_url(url)
        assert reference.provider is MusicProviderName.YOUTUBE_MUSIC
        metadata = await provider.get_metadata(reference.source_url)
        assert metadata.provider is MusicProviderName.YOUTUBE_MUSIC
        check = await provider.check_source(metadata.provider, metadata.provider_track_id)
        assert check.status is ProviderRuntimeStatus.AVAILABLE
        prepared = await provider.download_source(
            metadata.provider,
            metadata.provider_track_id,
            job_id,
            1,
            timeout_seconds=120,
        )
        assert prepared.file_path is not None
        probed = await MediaProbe(root).probe(
            prepared.file_path,
            provider=prepared.provider,
            provider_track_id=prepared.provider_track_id,
            native_encoded=prepared.native_encoded,
            provider_decrypted=prepared.provider_decrypted,
        )
        assert probed.codec is NativeCodec.AAC
        assert probed.container is NativeContainer.M4A
        assert probed.bitrate_kbps is not None
        assert abs(probed.bitrate_kbps - 128) <= 26
    finally:
        await provider.close()
