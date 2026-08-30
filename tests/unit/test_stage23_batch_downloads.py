from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from app.core.delivery_targets import DeliveryTargetType, GroupChatTarget
from app.core.download import DownloadDeliveryTarget
from app.core.download_preferences import UserDownloadPreferences
from app.core.enums import BatchSourceType, BatchStatus, MusicProviderName
from app.core.models import ResolvedCollection, ResolvedCollectionItem
from app.core.telegram_context import TelegramChatType, TelegramContext
from app.providers.onthespot.provider import OnTheSpotProvider
from app.services.batch_download import ActiveBatchLimitExceeded, BatchDownloadService


def _collection(*ids: str) -> ResolvedCollection:
    return ResolvedCollection(
        source_type=BatchSourceType.PLAYLIST,
        provider=MusicProviderName.SPOTIFY,
        collection_id="playlist-1",
        source_reference="https://example.invalid/playlist/1",
        title="Playlist",
        creator="Artist",
        items=tuple(
            ResolvedCollectionItem(position=i, provider_media_id=value, title=value)
            for i, value in enumerate(ids, 1)
        ),
    )


@dataclass
class _Resolver:
    collection: ResolvedCollection

    async def resolve_collection(self, source_type: BatchSourceType, source_reference: str):
        return self.collection


@pytest.mark.asyncio
async def test_snapshot_preserves_order_and_duplicates_and_is_idempotent(database):
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(101)
    service = BatchDownloadService(database, _Resolver(_collection("a", "x", "a")))
    preferences = UserDownloadPreferences(user.id)
    first = await service.expand(
        user_id=user.id,
        confirmation_id="confirm-1",
        source_type=BatchSourceType.PLAYLIST,
        source_reference="playlist",
        preferences=preferences,
    )
    second = await service.expand(
        user_id=user.id,
        confirmation_id="confirm-1",
        source_type=BatchSourceType.PLAYLIST,
        source_reference="playlist",
        preferences=preferences,
    )
    assert first.id == second.id
    async with database.transaction() as repositories:
        items = await repositories.batch_download.list_items(first.id)
    assert [(item.position, item.provider_media_id) for item in items] == [
        (1, "a"),
        (2, "x"),
        (3, "a"),
    ]


@pytest.mark.asyncio
async def test_concurrent_confirmation_creates_one_batch(database):
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(102)
    service = BatchDownloadService(database, _Resolver(_collection("a", "b")))
    preferences = UserDownloadPreferences(user.id)

    async def expand():
        return await service.expand(
            user_id=user.id,
            confirmation_id="same-confirmation",
            source_type=BatchSourceType.PLAYLIST,
            source_reference="playlist",
            preferences=preferences,
        )

    results = await asyncio.gather(expand(), expand())
    assert results[0].id == results[1].id
    async with database.transaction() as repositories:
        assert len(await repositories.batch_download.list_items(results[0].id)) == 2


@pytest.mark.asyncio
async def test_active_batch_limit_is_enforced(database):
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(105)
    service = BatchDownloadService(
        database, _Resolver(_collection("a")), max_active_batches_per_user=1
    )
    await service.expand(
        user_id=user.id,
        confirmation_id="active-1",
        source_type=BatchSourceType.PLAYLIST,
        source_reference="playlist",
        preferences=UserDownloadPreferences(user.id),
    )
    with pytest.raises(ActiveBatchLimitExceeded):
        await service.expand(
            user_id=user.id,
            confirmation_id="active-2",
            source_type=BatchSourceType.PLAYLIST,
            source_reference="playlist",
            preferences=UserDownloadPreferences(user.id),
        )


@pytest.mark.asyncio
async def test_child_admission_is_single_and_forces_private_user(database):
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(103)
    calls: list[DownloadDeliveryTarget] = []

    async def admit(request, *, target):
        calls.append(target)
        return type("Result", (), {"request_id": None})()

    service = BatchDownloadService(database, _Resolver(_collection("a")), child_admitter=admit)
    batch = await service.expand(
        user_id=user.id,
        confirmation_id="confirm-admit",
        source_type=BatchSourceType.PLAYLIST,
        source_reference="playlist",
        preferences=UserDownloadPreferences(user.id),
    )
    target = DownloadDeliveryTarget(
        user_id=user.id,
        context=TelegramContext(user.id, -100, TelegramChatType.GROUP),
        delivery_target=GroupChatTarget(-100),
        source_message_id=1,
    )
    assert await service.admit_pending(batch.id, target=target) == 1
    assert calls[0].delivery_target.target_type is DeliveryTargetType.PRIVATE_USER
    assert await service.admit_pending(batch.id, target=target) == 0


def test_playlist_urls_are_explicitly_classified():
    provider = OnTheSpotProvider.__new__(OnTheSpotProvider)
    reference = provider.detect_media("https://open.spotify.com/playlist/0123456789012345678901")
    assert reference.provider is MusicProviderName.SPOTIFY
    assert reference.provider_playlist_id == "0123456789012345678901"


@pytest.mark.asyncio
async def test_reconcile_cancel_waits_for_active_child(database):
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(104)
    service = BatchDownloadService(database, _Resolver(_collection("a")))
    batch = await service.expand(
        user_id=user.id,
        confirmation_id="confirm-cancel",
        source_type=BatchSourceType.PLAYLIST,
        source_reference="playlist",
        preferences=UserDownloadPreferences(user.id),
    )
    assert await service.cancel(batch.id, user_id=user.id)
    assert await service.reconcile(batch.id) is BatchStatus.CANCELLED
