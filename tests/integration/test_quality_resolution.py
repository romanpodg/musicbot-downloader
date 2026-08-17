from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.core.enums import (
    DownloadPlanOperation,
    DownloadPlanReadiness,
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    ProviderResolutionStatus,
    ProviderRuntimeStatus,
    QualityCandidateRejectionReason,
    QualityProfile,
    QualityResolutionStatus,
)
from app.core.models import (
    DownloadProviderCandidate,
    NativeMediaInfo,
    ProviderCandidateFailure,
    ProviderCapabilities,
    ProviderResolutionResult,
    ProviderSourceCheck,
)
from app.providers.base import MusicProvider, ProviderAvailability, TrackReference
from app.providers.onthespot.capabilities import ONTHESPOT_CAPABILITIES
from app.services.provider_resolution import ProviderResolver
from app.services.quality_resolution import QualityResolver
from app.storage import Database


class SnapshotProviderResolver:
    def __init__(self, snapshots: Sequence[ProviderResolutionResult]) -> None:
        self._snapshots = list(snapshots)
        self.calls: list[int] = []

    async def resolve(self, track_id: int) -> ProviderResolutionResult:
        self.calls.append(track_id)
        return self._snapshots.pop(0)


class MutableRuntimeProvider(MusicProvider):
    def __init__(self) -> None:
        self.checks: dict[MusicProviderName, ProviderSourceCheck] = {}
        self.calls: list[MusicProviderName] = []

    async def availability(self) -> ProviderAvailability:
        return ProviderAvailability(True)

    def detect_url(self, url: str) -> TrackReference:
        raise AssertionError("Quality resolution must not resolve URLs")

    async def get_metadata(self, url: str) -> object:  # type: ignore[override]
        raise AssertionError("Quality resolution must not retrieve metadata")

    async def list_searchable_providers(self) -> tuple[MusicProviderName, ...]:
        raise AssertionError("Quality resolution must not perform discovery")

    async def search_tracks(self, request: object) -> list[object]:  # type: ignore[override]
        raise AssertionError("Quality resolution must not perform discovery")

    def provider_capabilities(self, provider: MusicProviderName) -> ProviderCapabilities:
        return ONTHESPOT_CAPABILITIES[provider]

    async def check_source(
        self,
        provider: MusicProviderName,
        provider_track_id: str,
    ) -> ProviderSourceCheck:
        self.calls.append(provider)
        return self.checks[provider]


def _candidate(
    provider: MusicProviderName,
    native: NativeMediaInfo | None,
    *,
    source_id: int | None = None,
) -> DownloadProviderCandidate:
    return DownloadProviderCandidate(
        track_id=42,
        track_source_id=source_id or list(MusicProviderName).index(provider) + 1,
        provider=provider,
        provider_track_id=f"{provider.value}-track",
        runtime_status=ProviderRuntimeStatus.AVAILABLE,
        capabilities=ONTHESPOT_CAPABILITIES[provider],
        native_media_info=native,
    )


def _failure(
    provider: MusicProviderName,
    status: ProviderRuntimeStatus,
) -> ProviderCandidateFailure:
    return ProviderCandidateFailure(
        track_id=42,
        track_source_id=list(MusicProviderName).index(provider) + 1,
        provider=provider,
        provider_track_id=f"{provider.value}-track",
        runtime_status=status,
        capabilities=ONTHESPOT_CAPABILITIES[provider],
        error_code=status.value.lower(),
    )


def _snapshot(
    candidates: Sequence[DownloadProviderCandidate] = (),
    failures: Sequence[ProviderCandidateFailure] = (),
) -> ProviderResolutionResult:
    status = (
        ProviderResolutionStatus.AVAILABLE
        if candidates
        else ProviderResolutionStatus.NO_AVAILABLE_PROVIDER
    )
    return ProviderResolutionResult(42, status, tuple(candidates), tuple(failures))


async def _persist_track_sources(
    database: Database,
    providers: Sequence[MusicProviderName],
) -> int:
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
async def test_real_provider_resolver_is_called_fresh_for_dynamic_auth(
    database: Database,
) -> None:
    providers = (
        MusicProviderName.QOBUZ,
        MusicProviderName.DEEZER,
        MusicProviderName.SPOTIFY,
    )
    track_id = await _persist_track_sources(database, providers)
    runtime = MutableRuntimeProvider()
    runtime.checks = {
        MusicProviderName.QOBUZ: ProviderSourceCheck(
            ProviderRuntimeStatus.AVAILABLE,
            NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
        ),
        MusicProviderName.DEEZER: ProviderSourceCheck(
            ProviderRuntimeStatus.AVAILABLE,
            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 320),
        ),
        MusicProviderName.SPOTIFY: ProviderSourceCheck(
            ProviderRuntimeStatus.AUTH_REQUIRED,
            error_code="authentication_required",
        ),
    }
    resolver = QualityResolver(ProviderResolver(database, runtime))

    first = await resolver.resolve(track_id, QualityProfile.MP3_320)
    runtime.checks[MusicProviderName.QOBUZ] = ProviderSourceCheck(
        ProviderRuntimeStatus.AUTH_REQUIRED,
        error_code="authentication_required",
    )
    runtime.checks[MusicProviderName.SPOTIFY] = ProviderSourceCheck(
        ProviderRuntimeStatus.AVAILABLE,
        NativeMediaInfo(NativeCodec.VORBIS, NativeContainer.OGG, 320),
    )
    second = await resolver.resolve(track_id, QualityProfile.MP3_320)

    assert (
        runtime.calls
        == [
            MusicProviderName.DEEZER,
            MusicProviderName.QOBUZ,
            MusicProviderName.SPOTIFY,
        ]
        * 2
    )
    assert [plan.provider for plan in first.plans] == [
        MusicProviderName.DEEZER,
        MusicProviderName.QOBUZ,
    ]
    assert [plan.provider for plan in second.plans] == [MusicProviderName.DEEZER]


@pytest.mark.asyncio
async def test_only_currently_available_provider_can_create_plan() -> None:
    resolver = SnapshotProviderResolver(
        [
            _snapshot(
                [_candidate(MusicProviderName.DEEZER, NativeMediaInfo(NativeCodec.MP3, None, 320))],
                [_failure(MusicProviderName.QOBUZ, ProviderRuntimeStatus.AUTH_REQUIRED)],
            )
        ]
    )
    result = await QualityResolver(resolver).resolve(42, QualityProfile.MP3_320)

    assert result.status is QualityResolutionStatus.RESOLVED
    assert [plan.provider for plan in result.plans] == [MusicProviderName.DEEZER]
    qobuz = next(
        item for item in result.provider_diagnostics if item.provider is MusicProviderName.QOBUZ
    )
    assert qobuz.rejection_reason is QualityCandidateRejectionReason.PROVIDER_NOT_AVAILABLE


@pytest.mark.asyncio
async def test_dynamic_authentication_is_resolved_fresh_on_every_call() -> None:
    qobuz = _candidate(
        MusicProviderName.QOBUZ,
        NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
    )
    deezer = _candidate(
        MusicProviderName.DEEZER,
        NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 320),
    )
    spotify = _candidate(
        MusicProviderName.SPOTIFY,
        NativeMediaInfo(NativeCodec.VORBIS, NativeContainer.OGG, 320),
    )
    resolver = SnapshotProviderResolver(
        [
            _snapshot(
                [qobuz, deezer],
                [_failure(MusicProviderName.SPOTIFY, ProviderRuntimeStatus.AUTH_REQUIRED)],
            ),
            _snapshot(
                [deezer, spotify],
                [_failure(MusicProviderName.QOBUZ, ProviderRuntimeStatus.AUTH_REQUIRED)],
            ),
        ]
    )
    quality_resolver = QualityResolver(resolver)

    first = await quality_resolver.resolve(42, QualityProfile.MP3_320)
    second = await quality_resolver.resolve(42, QualityProfile.MP3_320)

    assert resolver.calls == [42, 42]
    assert [plan.provider for plan in first.plans] == [
        MusicProviderName.DEEZER,
        MusicProviderName.QOBUZ,
    ]
    assert [plan.provider for plan in second.plans] == [MusicProviderName.DEEZER]
    assert (
        next(
            item for item in second.provider_diagnostics if item.provider is MusicProviderName.QOBUZ
        ).runtime_status
        is ProviderRuntimeStatus.AUTH_REQUIRED
    )


@pytest.mark.asyncio
async def test_no_available_provider_is_distinct_from_quality_unavailable() -> None:
    no_provider = await QualityResolver(
        SnapshotProviderResolver(
            [
                _snapshot(
                    failures=[
                        _failure(MusicProviderName.QOBUZ, ProviderRuntimeStatus.AUTH_REQUIRED),
                        _failure(MusicProviderName.DEEZER, ProviderRuntimeStatus.UNAVAILABLE),
                        _failure(MusicProviderName.TIDAL, ProviderRuntimeStatus.ERROR),
                    ]
                )
            ]
        )
    ).resolve(42, QualityProfile.LOSSLESS)
    no_quality = await QualityResolver(
        SnapshotProviderResolver(
            [
                _snapshot(
                    [
                        _candidate(
                            MusicProviderName.SPOTIFY,
                            NativeMediaInfo(NativeCodec.VORBIS, NativeContainer.OGG, 320),
                        ),
                        _candidate(
                            MusicProviderName.YOUTUBE_MUSIC,
                            NativeMediaInfo(NativeCodec.AAC, NativeContainer.M4A, 128),
                        ),
                    ]
                )
            ]
        )
    ).resolve(42, QualityProfile.LOSSLESS)

    assert no_provider.status is QualityResolutionStatus.NO_AVAILABLE_PROVIDER
    assert no_quality.status is QualityResolutionStatus.QUALITY_UNAVAILABLE
    assert no_provider.plans == no_quality.plans == ()


@pytest.mark.asyncio
async def test_native_exact_outranks_confirmed_lossless_transcode() -> None:
    result = await QualityResolver(
        SnapshotProviderResolver(
            [
                _snapshot(
                    [
                        _candidate(
                            MusicProviderName.QOBUZ,
                            NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
                        ),
                        _candidate(
                            MusicProviderName.DEEZER,
                            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 320),
                        ),
                    ]
                )
            ]
        )
    ).resolve(42, QualityProfile.MP3_320)

    assert [(plan.provider, plan.operation) for plan in result.plans] == [
        (MusicProviderName.DEEZER, DownloadPlanOperation.DIRECT),
        (MusicProviderName.QOBUZ, DownloadPlanOperation.TRANSCODE),
    ]
    assert result.primary_plan is result.plans[0]
    assert result.fallback_plans == result.plans[1:]


@pytest.mark.asyncio
async def test_confirmed_lossless_outranks_preflight_lossless() -> None:
    result = await QualityResolver(
        SnapshotProviderResolver(
            [
                _snapshot(
                    [
                        _candidate(MusicProviderName.TIDAL, None),
                        _candidate(
                            MusicProviderName.QOBUZ,
                            NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
                        ),
                    ]
                )
            ]
        )
    ).resolve(42, QualityProfile.LOSSLESS)

    assert [(plan.provider, plan.readiness) for plan in result.plans] == [
        (MusicProviderName.QOBUZ, DownloadPlanReadiness.CONFIRMED),
        (MusicProviderName.TIDAL, DownloadPlanReadiness.REQUIRES_PREFLIGHT),
    ]
    assert result.plans[1].source_expectation.required_lossless is True


@pytest.mark.asyncio
async def test_original_spotify_source_gets_no_ranking_preference() -> None:
    result = await QualityResolver(
        SnapshotProviderResolver(
            [
                _snapshot(
                    [
                        _candidate(
                            MusicProviderName.SPOTIFY,
                            NativeMediaInfo(NativeCodec.VORBIS, NativeContainer.OGG, 320),
                            source_id=1,
                        ),
                        _candidate(
                            MusicProviderName.DEEZER,
                            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 320),
                        ),
                        _candidate(
                            MusicProviderName.QOBUZ,
                            NativeMediaInfo(NativeCodec.FLAC, NativeContainer.FLAC),
                        ),
                    ]
                )
            ]
        )
    ).resolve(42, QualityProfile.MP3_320)

    assert [plan.provider for plan in result.plans] == [
        MusicProviderName.DEEZER,
        MusicProviderName.QOBUZ,
    ]


@pytest.mark.asyncio
async def test_partial_provider_failures_remain_diagnostics() -> None:
    result = await QualityResolver(
        SnapshotProviderResolver(
            [
                _snapshot(
                    [
                        _candidate(
                            MusicProviderName.DEEZER,
                            NativeMediaInfo(NativeCodec.MP3, NativeContainer.MP3, 320),
                        )
                    ],
                    [
                        _failure(MusicProviderName.QOBUZ, ProviderRuntimeStatus.ERROR),
                        _failure(MusicProviderName.TIDAL, ProviderRuntimeStatus.AUTH_REQUIRED),
                    ],
                )
            ]
        )
    ).resolve(42, QualityProfile.MP3_320)

    assert result.status is QualityResolutionStatus.RESOLVED
    assert [plan.provider for plan in result.plans] == [MusicProviderName.DEEZER]
    assert {item.provider for item in result.provider_diagnostics} == {
        MusicProviderName.DEEZER,
        MusicProviderName.QOBUZ,
        MusicProviderName.TIDAL,
    }
