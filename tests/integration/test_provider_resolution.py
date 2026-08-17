from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.enums import (
    MusicProviderName,
    ProviderResolutionStatus,
    ProviderRuntimeStatus,
)
from app.core.exceptions import TrackNotFound
from app.core.models import (
    ProviderCapabilities,
    ProviderMediaCapabilities,
    ProviderSourceCheck,
)
from app.providers.base import MusicProvider, ProviderAvailability, TrackReference
from app.services.provider_resolution import ProviderResolver
from app.storage import Database

_SUPPORTED = ProviderCapabilities(
    metadata_supported=True,
    search_supported=True,
    download_supported=True,
    requires_auth=False,
    media=ProviderMediaCapabilities(known=False),
)


class RuntimeProvider(MusicProvider):
    def __init__(self) -> None:
        self.checks: dict[MusicProviderName, ProviderSourceCheck | Exception] = {}
        self.capabilities: dict[MusicProviderName, ProviderCapabilities] = {}
        self.capability_errors: set[MusicProviderName] = set()
        self.calls: list[MusicProviderName] = []

    async def availability(self) -> ProviderAvailability:
        return ProviderAvailability(True)

    def detect_url(self, url: str) -> TrackReference:
        raise AssertionError("Stage 4 must not resolve URLs")

    async def get_metadata(self, url: str) -> object:  # type: ignore[override]
        raise AssertionError("Stage 4 must not invoke Stage 3 metadata")

    async def list_searchable_providers(self) -> tuple[MusicProviderName, ...]:
        raise AssertionError("Stage 4 must not perform provider discovery")

    async def search_tracks(self, request: object) -> list[object]:  # type: ignore[override]
        raise AssertionError("Stage 4 must not perform provider discovery")

    def provider_capabilities(self, provider: MusicProviderName) -> ProviderCapabilities:
        if provider in self.capability_errors:
            raise RuntimeError("simulated capability bug")
        return self.capabilities.get(provider, _SUPPORTED)

    async def check_source(
        self, provider: MusicProviderName, provider_track_id: str
    ) -> ProviderSourceCheck:
        self.calls.append(provider)
        result = self.checks[provider]
        if isinstance(result, Exception):
            raise result
        return result


async def _track_with_sources(database: Database, providers: tuple[MusicProviderName, ...]) -> int:
    async with database.transaction() as repositories:
        track = await repositories.tracks.create_track(title="Track", artist="Artist")
        for index, provider in enumerate(providers, 1):
            await repositories.track_sources.upsert_source(
                track_id=track.id,
                provider=provider,
                provider_track_id=f"source-{index}",
                url=None,
            )
        return track.id


@pytest.mark.asyncio
async def test_four_sources_produce_three_candidates_and_one_auth_failure(
    database: Database,
) -> None:
    track_id = await _track_with_sources(
        database,
        (
            MusicProviderName.SPOTIFY,
            MusicProviderName.DEEZER,
            MusicProviderName.QOBUZ,
            MusicProviderName.TIDAL,
        ),
    )
    provider = RuntimeProvider()
    provider.checks = {
        MusicProviderName.SPOTIFY: ProviderSourceCheck(ProviderRuntimeStatus.AVAILABLE),
        MusicProviderName.DEEZER: ProviderSourceCheck(ProviderRuntimeStatus.AVAILABLE),
        MusicProviderName.QOBUZ: ProviderSourceCheck(
            ProviderRuntimeStatus.AUTH_REQUIRED,
            error_code="authentication_required",
        ),
        MusicProviderName.TIDAL: ProviderSourceCheck(ProviderRuntimeStatus.AVAILABLE),
    }

    result = await ProviderResolver(database, provider).resolve(track_id)

    assert result.status is ProviderResolutionStatus.AVAILABLE
    assert [item.provider for item in result.candidates] == [
        MusicProviderName.DEEZER,
        MusicProviderName.SPOTIFY,
        MusicProviderName.TIDAL,
    ]
    assert [item.provider for item in result.failures] == [MusicProviderName.QOBUZ]
    assert result.failures[0].runtime_status is ProviderRuntimeStatus.AUTH_REQUIRED
    assert not hasattr(result.candidates[0], "preferred")


@pytest.mark.asyncio
async def test_track_without_sources_is_a_non_exceptional_no_provider_result(
    database: Database,
) -> None:
    track_id = await _track_with_sources(database, ())
    result = await ProviderResolver(database, RuntimeProvider()).resolve(track_id)
    assert result.status is ProviderResolutionStatus.NO_AVAILABLE_PROVIDER
    assert result.candidates == ()
    assert result.failures == ()


@pytest.mark.asyncio
async def test_unsupported_source_is_reported_without_runtime_probe(database: Database) -> None:
    track_id = await _track_with_sources(database, (MusicProviderName.APPLE_MUSIC,))
    provider = RuntimeProvider()
    provider.capabilities[MusicProviderName.APPLE_MUSIC] = replace(
        _SUPPORTED,
        download_supported=False,
    )
    result = await ProviderResolver(database, provider).resolve(track_id)
    assert result.status is ProviderResolutionStatus.NO_AVAILABLE_PROVIDER
    assert result.failures[0].runtime_status is ProviderRuntimeStatus.UNSUPPORTED
    assert provider.calls == []


@pytest.mark.asyncio
async def test_partial_provider_failure_does_not_abort_other_sources(database: Database) -> None:
    track_id = await _track_with_sources(
        database,
        (MusicProviderName.SPOTIFY, MusicProviderName.DEEZER, MusicProviderName.QOBUZ),
    )
    provider = RuntimeProvider()
    provider.checks = {
        MusicProviderName.SPOTIFY: RuntimeError("simulated upstream bug"),
        MusicProviderName.DEEZER: ProviderSourceCheck(ProviderRuntimeStatus.AVAILABLE),
        MusicProviderName.QOBUZ: ProviderSourceCheck(
            ProviderRuntimeStatus.AUTH_REQUIRED,
            error_code="authentication_required",
        ),
    }
    result = await ProviderResolver(database, provider).resolve(track_id)
    assert [item.provider for item in result.candidates] == [MusicProviderName.DEEZER]
    assert [(item.provider, item.runtime_status) for item in result.failures] == [
        (MusicProviderName.QOBUZ, ProviderRuntimeStatus.AUTH_REQUIRED),
        (MusicProviderName.SPOTIFY, ProviderRuntimeStatus.ERROR),
    ]


@pytest.mark.asyncio
async def test_missing_track_is_the_only_expected_application_abort(database: Database) -> None:
    with pytest.raises(TrackNotFound):
        await ProviderResolver(database, RuntimeProvider()).resolve(999)


@pytest.mark.asyncio
async def test_capability_failure_is_isolated_to_one_source(database: Database) -> None:
    track_id = await _track_with_sources(
        database,
        (MusicProviderName.SPOTIFY, MusicProviderName.DEEZER),
    )
    provider = RuntimeProvider()
    provider.capability_errors.add(MusicProviderName.SPOTIFY)
    provider.checks[MusicProviderName.DEEZER] = ProviderSourceCheck(ProviderRuntimeStatus.AVAILABLE)
    result = await ProviderResolver(database, provider).resolve(track_id)
    assert [item.provider for item in result.candidates] == [MusicProviderName.DEEZER]
    assert result.failures[0].provider is MusicProviderName.SPOTIFY
    assert result.failures[0].runtime_status is ProviderRuntimeStatus.ERROR
    assert result.failures[0].capabilities.media.known is False
    assert result.failures[0].error_code == "capability_query_failed"
