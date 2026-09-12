"""Stage 30.6.1 provider-neutral collection admission regressions."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.core.delivery_targets import GroupChatTarget
from app.core.download import DownloadDeliveryTarget, DownloadRequest
from app.core.download_preferences import UserDownloadPreferences
from app.core.enums import BatchItemStatus, BatchSourceType, MusicProviderName
from app.core.exceptions import MetadataUnavailable, ProviderUnavailable
from app.core.models import ProviderMediaCapabilities, ResolvedCollection, ResolvedCollectionItem
from app.core.provider_resolution import (
    CanonicalMediaIdentity,
    CanonicalTrackAdmission,
    ProviderCandidate,
    ProviderCandidateRanker,
    match_media,
)
from app.core.telegram_context import TelegramChatType, TelegramContext
from app.services.batch_download import BatchDownloadService
from app.services.download_lifecycle import DownloadLifecycleService


def _collection(
    provider: MusicProviderName, *items: str, collection_id: str = "collection-1"
) -> ResolvedCollection:
    return ResolvedCollection(
        source_type=BatchSourceType.PLAYLIST,
        provider=provider,
        collection_id=collection_id,
        source_reference=f"https://example.invalid/{provider.value}/{collection_id}",
        title="Provider-neutral collection",
        creator="Collection creator",
        items=tuple(
            ResolvedCollectionItem(
                position=position,
                provider_media_id=item,
                title="Canonical recording",
                artist="Canonical artist",
                duration_ms=180_000,
            )
            for position, item in enumerate(items, 1)
        ),
    )


@dataclass
class _CollectionResolver:
    collection: ResolvedCollection

    async def resolve_collection(self, *_: object) -> ResolvedCollection:
        return self.collection


@dataclass
class _AdmissionResolver:
    admission: CanonicalTrackAdmission
    error: Exception | None = None
    calls: list[tuple[MusicProviderName, str]] | None = None

    async def resolve(
        self, *, collection_provider: MusicProviderName, provider_media_id: str
    ) -> CanonicalTrackAdmission:
        if self.calls is not None:
            self.calls.append((collection_provider, provider_media_id))
        if self.error is not None:
            raise self.error
        return self.admission


def _target(telegram_id: int) -> DownloadDeliveryTarget:
    return DownloadDeliveryTarget(
        user_id=telegram_id,
        context=TelegramContext(telegram_id, -100_3061, TelegramChatType.GROUP),
        delivery_target=GroupChatTarget(-100_3061),
        source_message_id=3061,
    )


async def _canonical_admission(database) -> CanonicalTrackAdmission:  # type: ignore[no-untyped-def]
    async with database.transaction() as repositories:
        track = await repositories.tracks.create_track(
            title="Canonical recording",
            artist="Canonical artist",
            duration_ms=180_000,
            isrc="USABC1234567",
        )
    return CanonicalTrackAdmission(
        track.id,
        CanonicalMediaIdentity.from_values(
            title="Canonical recording",
            artist="Canonical artist",
            duration_ms=180_000,
            isrc="USABC1234567",
        ),
    )


@pytest.mark.asyncio
async def test_collection_admission_is_neutral_but_collection_provenance_remains_durable(database):  # type: ignore[no-untyped-def]
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(306_101)
    admission = await _canonical_admission(database)
    calls: list[DownloadRequest] = []
    lifecycle = DownloadLifecycleService(database)

    async def child(request: DownloadRequest, *, target: DownloadDeliveryTarget):
        calls.append(request)
        durable = await lifecycle.admit(
            confirmation_id=request.confirmation_id or "missing",
            request=request,
            canonical_track_id=admission.track_id,
            target=target,
        )
        return SimpleNamespace(request_id=durable.request.id)

    collection = _collection(MusicProviderName.APPLE_MUSIC, "apple-item")
    service = BatchDownloadService(
        database,
        _CollectionResolver(collection),
        child_admitter=child,
        item_admission_resolver=_AdmissionResolver(admission),
    )
    batch = await service.create_from_collection(
        user_id=user.id,
        confirmation_id="stage3061-apple",
        collection=collection,
        preferences=UserDownloadPreferences(user.id),
    )

    assert await service.admit_pending(batch.id, target=_target(user.telegram_id)) == 1
    assert await service.admit_pending(batch.id, target=_target(user.telegram_id)) == 0
    assert len(calls) == 1
    assert calls[0].recognized_track is None
    assert calls[0].canonical_admission == admission

    async with database.transaction() as repositories:
        durable_batch = await repositories.batch_download.get(batch.id)
        items = await repositories.batch_download.list_items(batch.id)
        request = await repositories.download_lifecycle.get_by_confirmation("stage3061-apple:1")
    assert durable_batch is not None
    assert durable_batch.provider is MusicProviderName.APPLE_MUSIC
    assert durable_batch.source_collection_id == "collection-1"
    assert [(item.position, item.provider_media_id) for item in items] == [(1, "apple-item")]
    assert items[0].download_request_id is not None
    assert request is not None
    assert request.provider is None
    assert request.provider_media_id is None


@pytest.mark.asyncio
async def test_two_collection_providers_for_one_recording_have_identical_stage25_ordering(database):  # type: ignore[no-untyped-def]
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(306_102)
    admission = await _canonical_admission(database)
    lifecycle = DownloadLifecycleService(database)

    async def child(request: DownloadRequest, *, target: DownloadDeliveryTarget):
        durable = await lifecycle.admit(
            confirmation_id=request.confirmation_id or "missing",
            request=request,
            canonical_track_id=admission.track_id,
            target=target,
        )
        return SimpleNamespace(request_id=durable.request.id)

    batches = []
    for provider, item_id in (
        (MusicProviderName.APPLE_MUSIC, "apple-item"),
        (MusicProviderName.QOBUZ, "qobuz-item"),
    ):
        collection = _collection(provider, item_id, collection_id=f"{provider.value}-collection")
        service = BatchDownloadService(
            database,
            _CollectionResolver(collection),
            child_admitter=child,
            item_admission_resolver=_AdmissionResolver(admission),
        )
        batch = await service.create_from_collection(
            user_id=user.id,
            confirmation_id=f"stage3061-{provider.value}",
            collection=collection,
            preferences=UserDownloadPreferences(user.id),
        )
        assert await service.admit_pending(batch.id, target=_target(user.telegram_id)) == 1
        batches.append(batch)

    async with database.transaction() as repositories:
        requests = tuple(
            [
                await repositories.download_lifecycle.get_by_confirmation(
                    f"stage3061-{provider.value}:1"
                )
                for provider in (MusicProviderName.APPLE_MUSIC, MusicProviderName.QOBUZ)
            ]
        )
    assert all(request is not None and request.provider is None for request in requests)

    identity = admission.identity
    candidates = tuple(
        ProviderCandidate(
            provider,
            media_id,
            identity,
            match_media(identity, identity),
            # Capabilities are irrelevant to this ordering proof.
            ProviderMediaCapabilities(supports_lossy=True),
        )
        for provider, media_id in (
            (MusicProviderName.QOBUZ, "qobuz-result"),
            (MusicProviderName.APPLE_MUSIC, "apple-result"),
        )
    )
    ranker = ProviderCandidateRanker()
    ranked = tuple(
        ranker.rank(candidates, source_provider=request.provider if request else None)
        for request in requests
    )
    assert ranked[0] == ranked[1]
    assert [candidate.provider for candidate in ranked[0]] == [
        MusicProviderName.APPLE_MUSIC,
        MusicProviderName.QOBUZ,
    ]
    assert [batch.provider for batch in batches] == [
        MusicProviderName.APPLE_MUSIC,
        MusicProviderName.QOBUZ,
    ]


@pytest.mark.asyncio
async def test_definitive_and_transient_resolution_failures_stay_pre_admission(database):  # type: ignore[no-untyped-def]
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(306_103)
    admission = await _canonical_admission(database)
    collection = _collection(MusicProviderName.QOBUZ, "qobuz-item")
    child_calls = 0

    async def child(*_: object, **__: object):
        nonlocal child_calls
        child_calls += 1
        raise AssertionError("a failing collection item must not create a child")

    for error, expected, retryable in (
        (MetadataUnavailable("not found"), "MEDIA_NOT_FOUND", False),
        (ProviderUnavailable("sensitive upstream detail"), "PROVIDER_UNAVAILABLE", True),
    ):
        service = BatchDownloadService(
            database,
            _CollectionResolver(collection),
            child_admitter=child,
            item_admission_resolver=_AdmissionResolver(admission, error=error),
        )
        batch = await service.create_from_collection(
            user_id=user.id,
            confirmation_id=f"stage3061-failure-{expected}",
            collection=collection,
            preferences=UserDownloadPreferences(user.id),
        )
        assert await service.admit_pending(batch.id, target=_target(user.telegram_id)) == 0
        async with database.transaction() as repositories:
            item = (await repositories.batch_download.list_items(batch.id))[0]
        assert item.status is BatchItemStatus.FAILED
        assert item.download_request_id is None
        assert item.error_code == expected
        assert item.error_message is None
        await service.reconcile(batch.id)
        retry = await service.retry_failed(batch.id, user_id=user.id)
        assert (retry is not None) is retryable

    assert child_calls == 0


@pytest.mark.asyncio
async def test_duplicate_collection_occurrences_are_admitted_independently(database):  # type: ignore[no-untyped-def]
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(306_104)
    admission = await _canonical_admission(database)
    lifecycle = DownloadLifecycleService(database)
    resolver_calls: list[tuple[MusicProviderName, str]] = []

    async def child(request: DownloadRequest, *, target: DownloadDeliveryTarget):
        durable = await lifecycle.admit(
            confirmation_id=request.confirmation_id or "missing",
            request=request,
            canonical_track_id=admission.track_id,
            target=target,
        )
        return SimpleNamespace(request_id=durable.request.id)

    collection = _collection(MusicProviderName.SPOTIFY, "same", "other", "same")
    service = BatchDownloadService(
        database,
        _CollectionResolver(collection),
        child_admitter=child,
        item_admission_resolver=_AdmissionResolver(admission, calls=resolver_calls),
    )
    batch = await service.create_from_collection(
        user_id=user.id,
        confirmation_id="stage3061-duplicates",
        collection=collection,
        preferences=UserDownloadPreferences(user.id),
    )
    assert await service.admit_pending(batch.id, target=_target(user.telegram_id)) == 3
    async with database.transaction() as repositories:
        items = await repositories.batch_download.list_items(batch.id)
    assert [(item.position, item.provider_media_id) for item in items] == [
        (1, "same"),
        (2, "other"),
        (3, "same"),
    ]
    assert len({item.download_request_id for item in items}) == 3
    assert resolver_calls == [
        (MusicProviderName.SPOTIFY, "same"),
        (MusicProviderName.SPOTIFY, "other"),
        (MusicProviderName.SPOTIFY, "same"),
    ]
