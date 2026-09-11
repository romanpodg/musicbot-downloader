"""Optional Apple Music smoke through the existing child-owned runtime boundary."""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

from app.core.enums import MusicProviderName, NativeCodec, NativeContainer, ProviderRuntimeStatus
from app.core.models import TrackSearchRequest
from app.core.provider_accounts import SensitiveValue
from app.providers.onthespot import OnTheSpotProvider
from app.providers.onthespot.process import OnTheSpotProcessClient
from app.services.media import MediaProbe


@pytest.mark.external
@pytest.mark.asyncio
async def test_apple_music_aac256_smoke_when_explicitly_enabled() -> None:
    """Exercise bounded Apple auth, search, source, native media, and provenance."""

    token = os.environ.get("APPLE_MUSIC_MEDIA_USER_TOKEN")
    query = os.environ.get("APPLE_MUSIC_EXTERNAL_QUERY")
    if not token or not query:
        pytest.skip(
            "APPLE_MUSIC_EXTERNAL_SMOKE=NOT_RUN "
            "(APPLE_MUSIC_MEDIA_USER_TOKEN and APPLE_MUSIC_EXTERNAL_QUERY are required)"
        )
    if shutil.which("ffprobe") is None:
        pytest.skip("APPLE_MUSIC_EXTERNAL_SMOKE=NOT_RUN (ffprobe is unavailable)")

    root = Path(tempfile.mkdtemp(prefix="musicbot-apple-music-smoke-"))
    client = OnTheSpotProcessClient(temp_dir=root)
    provider = OnTheSpotProvider(client)
    job_id = uuid.uuid4().hex
    try:
        authorization = await provider.authorize_apple_music_session(SensitiveValue(token))
        assert authorization.persisted
        results = await provider.search_tracks(
            TrackSearchRequest(MusicProviderName.APPLE_MUSIC, query, 1)
        )
        assert results and results[0].provider is MusicProviderName.APPLE_MUSIC
        selected = results[0]
        source = await provider.check_source(
            MusicProviderName.APPLE_MUSIC, selected.provider_track_id
        )
        assert source.status is ProviderRuntimeStatus.AVAILABLE
        prepared = await provider.download_source(
            MusicProviderName.APPLE_MUSIC,
            selected.provider_track_id,
            job_id,
            1,
            timeout_seconds=120,
        )
        assert prepared.file_path is not None
        assert prepared.provider is MusicProviderName.APPLE_MUSIC
        assert prepared.provider_decrypted is True
        media = await MediaProbe(root).probe(
            prepared.file_path,
            provider=prepared.provider,
            provider_track_id=prepared.provider_track_id,
            native_encoded=prepared.native_encoded,
            provider_decrypted=prepared.provider_decrypted,
        )
        assert media.codec is NativeCodec.AAC
        assert media.container is NativeContainer.M4A
        assert media.bitrate_kbps is not None
        assert abs(media.bitrate_kbps - 256) <= 26
    finally:
        await provider.close()
