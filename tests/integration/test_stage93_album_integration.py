from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update

from app.core.enums import (
    AlbumItemResolutionStatus,
    AlbumRequestStatus,
    DownloadPlanOperation,
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    QualityProfile,
    TelegramDeliveryStatus,
    TelegramMediaKind,
)
from app.core.exceptions import MetadataUnavailable
from app.core.models import (
    AlbumSnapshot,
    AlbumTrackSnapshot,
    DownloadArtifactMetadata,
    TelegramUploadReceipt,
)
from app.i18n import LocalizationService
from app.services.delivery import DeliveryPreparationService
from app.services.telegram_album_coordinator import TelegramAlbumCoordinator
from app.services.telegram_albums import (
    AlbumActionOutcome,
    TelegramAlbumRequestService,
)
from app.services.telegram_cache import TelegramFileCacheService
from app.services.telegram_delivery import TelegramDeliveryWorker
from app.services.telegram_requests import TelegramTrackRequestService
from app.storage import Database
from app.storage.models import (
    DownloadJob,
    JobSubscriber,
    TelegramAlbumItem,
    TelegramAlbumRequest,
    TelegramDeliveryRequest,
    TrackSource,
    UploadJob,
)
from app.storage.models.base import utc_now
from app.telegram import TelegramCachedMediaSpec, TelegramDeliveryReceipt


def _snapshot(count: int, *, duplicate_last: bool = False) -> AlbumSnapshot:
    return AlbumSnapshot(
        MusicProviderName.SPOTIFY,
        "album-1",
        "https://open.spotify.com/album/0123456789012345678901",
        "Synthetic Album",
        "Album Artist",
        tuple(
            AlbumTrackSnapshot(
                provider_track_id=(
                    "track-1" if duplicate_last and position == count else f"track-{position}"
                ),
                position=position,
                disc_number=1 if position <= 12 else 2,
                track_number=position if position <= 12 else position - 12,
                title=f"Track {position} 日本語",
                artist="Guest Artist" if position == 2 else "Album Artist",
                duration_ms=180_000,
                explicit=position == 2,
            )
            for position in range(1, count + 1)
        ),
        release_date="2026",
        duration_ms=count * 180_000,
    )


@dataclass
class AlbumResolver:
    snapshot: AlbumSnapshot
    calls: int = 0

    async def resolve_album(self, url: str) -> AlbumSnapshot:
        self.calls += 1
        return self.snapshot


class TrackResolver:
    def __init__(self, database: Database, failures: set[str] | None = None) -> None:
        self.database = database
        self.failures = failures or set()
        self.calls: list[str] = []

    async def resolve_provider_track(
        self,
        provider: MusicProviderName,
        provider_track_id: str,
        *,
        discover: bool,
    ) -> SimpleNamespace:
        self.calls.append(provider_track_id)
        if provider_track_id in self.failures:
            raise MetadataUnavailable()
        async with self.database.transaction() as repositories:
            existing = await repositories.track_sources.get_source(provider, provider_track_id)
            if existing is not None:
                track = await repositories.tracks.get_track_by_id(existing.track_id)
                assert track is not None
                return SimpleNamespace(track=track)
            track = await repositories.tracks.create_track(
                title=f"Resolved {provider_track_id}", artist="Artist"
            )
            await repositories.track_sources.upsert_source(
                track_id=track.id,
                provider=provider,
                provider_track_id=provider_track_id,
                url=None,
            )
            return SimpleNamespace(track=track)


class Gateway:
    def __init__(self) -> None:
        self.sent_files: list[str] = []
        self.summaries: list[str] = []
        self.message_id = 100

    async def send_text(self, chat_id: int, text: str) -> TelegramDeliveryReceipt:
        self.summaries.append(text)
        self.message_id += 1
        return TelegramDeliveryReceipt(chat_id, self.message_id)

    async def send_cached_audio(self, spec: TelegramCachedMediaSpec) -> TelegramDeliveryReceipt:
        self.sent_files.append(spec.file_id)
        self.message_id += 1
        return TelegramDeliveryReceipt(spec.chat_id, self.message_id)

    async def send_cached_document(self, spec: TelegramCachedMediaSpec) -> TelegramDeliveryReceipt:
        return await self.send_cached_audio(spec)


def _artifact(source: TrackSource) -> DownloadArtifactMetadata:
    return DownloadArtifactMetadata(
        source.id,
        source.provider,
        source.provider_track_id,
        DownloadPlanOperation.TRANSCODE,
        True,
        NativeCodec.FLAC,
        NativeContainer.FLAC,
        None,
        NativeCodec.MP3,
        NativeContainer.MP3,
        320,
        None,
        None,
        None,
        180_000,
        1234,
        "libmp3lame",
    )


async def _user(database: Database, telegram_id: int, quality: QualityProfile | None):
    async with database.transaction() as repositories:
        return await repositories.users.create_user(telegram_id, preferred_quality_profile=quality)


def _album_service(
    database: Database,
    resolver: AlbumResolver,
    wake: asyncio.Event | None = None,
) -> TelegramAlbumRequestService:
    return TelegramAlbumRequestService(
        database,
        resolver,  # type: ignore[arg-type]
        telegram_bot_id=100,
        wake_event=wake,
    )


def _coordinator(
    database: Database,
    resolver: TrackResolver,
    gateway: Gateway,
    album_wake: asyncio.Event | None = None,
    delivery_wake: asyncio.Event | None = None,
) -> TelegramAlbumCoordinator:
    return TelegramAlbumCoordinator(
        database,
        resolver,  # type: ignore[arg-type]
        gateway,  # type: ignore[arg-type]
        LocalizationService(("en", "ru"), "en"),
        album_wake_event=album_wake or asyncio.Event(),
        delivery_wake_event=delivery_wake or asyncio.Event(),
    )


async def _drain_album(coordinator: TelegramAlbumCoordinator) -> int:
    count = 0
    while True:
        item = await coordinator.claim(f"album-{count}")
        if item is None:
            return count
        await coordinator.process(item, f"album-{count}")
        count += 1


async def test_album_snapshot_no_action_and_persistent_multi_page_selection(
    database: Database,
) -> None:
    resolver = AlbumResolver(_snapshot(25))
    user = await _user(database, 9001, QualityProfile.MP3_320)
    service = _album_service(database, resolver)
    request = await service.request_album(
        user=user,
        telegram_chat_id=9001,
        source_message_id=1,
        url="https://example.test/album",
    )
    duplicate = await service.request_album(
        user=user,
        telegram_chat_id=9001,
        source_message_id=1,
        url="https://example.test/album",
    )
    assert duplicate.id == request.id
    assert resolver.calls == 1
    assert request.status is AlbumRequestStatus.AWAITING_ACTION
    async with database.transaction() as repositories:
        session = repositories.telegram_album._session  # noqa: SLF001
        assert await session.scalar(select(func.count(TelegramAlbumRequest.id))) == 1
        assert await session.scalar(select(func.count(TelegramAlbumItem.id))) == 25
        assert await session.scalar(select(func.count(DownloadJob.id))) == 0
        assert await session.scalar(select(func.count(JobSubscriber.id))) == 0
        assert await session.scalar(select(func.count(UploadJob.id))) == 0

    assert (await service.open_selection(request_id=request.id, telegram_user_id=9001)).accepted
    pages = [
        await service.selection_page(request_id=request.id, telegram_user_id=9001, page=page)
        for page in range(4)
    ]
    assert [len(page.items) if page else 0 for page in pages] == [8, 8, 8, 1]
    selected_items = [pages[0].items[0], pages[1].items[0], pages[3].items[0]]  # type: ignore[union-attr]
    for item in selected_items:
        assert (
            await service.toggle(
                request_id=request.id,
                item_id=item.id,
                telegram_user_id=9001,
            )
        ).accepted

    restarted = _album_service(database, resolver)
    for page_number in (0, 1, 3):
        page = await restarted.selection_page(
            request_id=request.id, telegram_user_id=9001, page=page_number
        )
        assert page is not None and page.selected_count == 3
        assert page.items[0].selected
    assert await restarted.select_all(request_id=request.id, telegram_user_id=9001, selected=True)
    assert (
        await restarted.selection_page(request_id=request.id, telegram_user_id=9001, page=2)
    ).selected_count == 25  # type: ignore[union-attr]
    assert (
        await restarted.select_all(request_id=request.id, telegram_user_id=9001, selected=False)
    ).accepted
    empty = await restarted.download_selected(request_id=request.id, telegram_user_id=9001)
    assert empty.outcome is AlbumActionOutcome.EMPTY
    async with database.transaction() as repositories:
        current = await repositories.telegram_album.get(request.id)
        assert current is not None
        assert current.status is AlbumRequestStatus.SELECTING_TRACKS
        assert await repositories.telegram_album.count_selected(request.id) == 0
        assert await repositories.telegram_delivery.get_by_album_item(selected_items[0].id) is None


async def test_first_album_quality_and_one_off_quality_are_safe_and_isolated(
    database: Database,
) -> None:
    resolver = AlbumResolver(_snapshot(3))
    new_user = await _user(database, 9002, None)
    service = _album_service(database, resolver)
    request = await service.request_album(
        user=new_user,
        telegram_chat_id=9002,
        source_message_id=1,
        url="https://example.test/album",
    )
    assert request.status is AlbumRequestStatus.AWAITING_QUALITY
    chosen = await service.choose_first_quality(
        request_id=request.id,
        telegram_user_id=9002,
        quality_profile=QualityProfile.AAC_256,
    )
    assert chosen.accepted and chosen.request is not None
    assert chosen.request.status is AlbumRequestStatus.AWAITING_ACTION
    assert chosen.request.quality_profile is QualityProfile.AAC_256
    async with database.transaction() as repositories:
        stored = await repositories.users.get_by_telegram_id(9002)
        assert stored is not None
        assert stored.preferred_quality_profile is QualityProfile.AAC_256
        assert await repositories.telegram_album.count_selected(request.id) == 0
        assert await repositories.telegram_delivery.get_by_album_item(1) is None

    assert (await service.open_quality(request_id=request.id, telegram_user_id=9002)).accepted
    restarted = _album_service(database, resolver)
    selected = await restarted.choose_quality(
        request_id=request.id,
        telegram_user_id=9002,
        quality_profile=QualityProfile.LOSSLESS,
    )
    assert selected.accepted and selected.request is not None
    assert selected.request.quality_profile is QualityProfile.LOSSLESS
    assert selected.request.status is AlbumRequestStatus.AWAITING_ACTION
    async with database.transaction() as repositories:
        stored = await repositories.users.get_by_telegram_id(9002)
        assert stored is not None
        assert stored.preferred_quality_profile is QualityProfile.AAC_256


async def test_download_selected_subset_expansion_restart_and_partial_failure(
    database: Database,
) -> None:
    user = await _user(database, 9003, QualityProfile.MP3_320)
    service = _album_service(database, AlbumResolver(_snapshot(12)), asyncio.Event())
    request = await service.request_album(
        user=user,
        telegram_chat_id=9003,
        source_message_id=1,
        url="https://example.test/album",
    )
    assert (await service.open_selection(request_id=request.id, telegram_user_id=9003)).accepted
    items = []
    for page_number in (0, 1):
        page = await service.selection_page(
            request_id=request.id, telegram_user_id=9003, page=page_number
        )
        assert page is not None
        items.extend(page.items)
    selected_positions = {2, 5, 9, 11}
    for item in items:
        if item.position in selected_positions:
            assert (
                await service.toggle(
                    request_id=request.id,
                    item_id=item.id,
                    telegram_user_id=9003,
                )
            ).accepted
    assert (await service.download_selected(request_id=request.id, telegram_user_id=9003)).accepted

    async with database.transaction() as repositories:
        known = await repositories.tracks.create_track(title="Known", artist="Artist")
        await repositories.track_sources.upsert_source(
            track_id=known.id,
            provider=MusicProviderName.SPOTIFY,
            provider_track_id="track-2",
            url="https://open.spotify.com/track/known00000000000000",
        )
    resolver = TrackResolver(database, {"track-9"})
    gateway = Gateway()
    first = _coordinator(database, resolver, gateway)
    item = await first.claim("first")
    assert item is not None and item.position == 2
    await first.process(item, "first")
    assert resolver.calls == []

    restarted = _coordinator(database, resolver, gateway)
    assert await _drain_album(restarted) == 3
    async with database.transaction() as repositories:
        session = repositories.telegram_album._session  # noqa: SLF001
        children = list(
            await session.scalars(
                select(TelegramDeliveryRequest)
                .where(TelegramDeliveryRequest.album_item_id.is_not(None))
                .order_by(TelegramDeliveryRequest.album_item_id)
            )
        )
        assert len(children) == 3
        assert {child.quality_profile for child in children} == {QualityProfile.MP3_320}
        assert (
            await session.scalar(
                select(func.count(TelegramAlbumItem.id)).where(
                    TelegramAlbumItem.resolution_status == AlbumItemResolutionStatus.FAILED
                )
            )
            == 1
        )
        assert await session.scalar(select(func.count(DownloadJob.id))) == 0
        await session.execute(
            update(TelegramDeliveryRequest).values(
                status=TelegramDeliveryStatus.DELIVERED, delivered_at=utc_now()
            )
        )
    assert await restarted.reconcile() == 1
    assert await restarted.reconcile() == 0
    async with database.transaction() as repositories:
        terminal = await repositories.telegram_album.get(request.id)
        assert terminal is not None
        assert terminal.status is AlbumRequestStatus.PARTIALLY_FAILED
    assert len(gateway.summaries) == 1


async def test_download_all_duplicate_position_reuses_track_singleflight(
    database: Database,
) -> None:
    user = await _user(database, 9004, QualityProfile.MP3_320)
    service = _album_service(database, AlbumResolver(_snapshot(3, duplicate_last=True)))
    request = await service.request_album(
        user=user,
        telegram_chat_id=9004,
        source_message_id=1,
        url="https://example.test/album",
    )
    assert (await service.download_all(request_id=request.id, telegram_user_id=9004)).accepted
    resolver = TrackResolver(database)
    gateway = Gateway()
    coordinator = _coordinator(database, resolver, gateway)
    assert await _drain_album(coordinator) == 3
    delivery = TelegramDeliveryWorker(
        database,
        DeliveryPreparationService(database, telegram_bot_id=100, max_size=1000),
        TelegramFileCacheService(database),
        gateway,  # type: ignore[arg-type]
        LocalizationService(("en", "ru"), "en"),
        max_attempts=3,
        wake_event=asyncio.Event(),
    )
    for number in range(3):
        child = await delivery.claim(f"delivery-{number}")
        assert child is not None
        await delivery.process(child, f"delivery-{number}")
    async with database.transaction() as repositories:
        session = repositories.singleflight._session  # noqa: SLF001
        assert await session.scalar(select(func.count(TelegramDeliveryRequest.id))) == 3
        assert await session.scalar(select(func.count(DownloadJob.id))) == 2
        assert await session.scalar(select(func.count(JobSubscriber.id))) == 3


async def test_callback_ownership_stale_states_and_action_races(database: Database) -> None:
    owner = await _user(database, 9005, QualityProfile.MP3_320)
    await _user(database, 9006, QualityProfile.LOSSLESS)
    service = _album_service(database, AlbumResolver(_snapshot(10)))
    request = await service.request_album(
        user=owner,
        telegram_chat_id=9005,
        source_message_id=1,
        url="https://example.test/album",
    )
    assert (
        await service.download_all(request_id=request.id, telegram_user_id=9006)
    ).outcome is AlbumActionOutcome.FORBIDDEN
    download, selection = await asyncio.gather(
        service.download_all(request_id=request.id, telegram_user_id=9005),
        service.open_selection(request_id=request.id, telegram_user_id=9005),
    )
    assert sum(result.accepted for result in (download, selection)) == 1
    async with database.transaction() as repositories:
        current = await repositories.telegram_album.get(request.id)
        assert current is not None
        assert current.status in {
            AlbumRequestStatus.QUEUED,
            AlbumRequestStatus.SELECTING_TRACKS,
        }
        items = await repositories.telegram_album.list_items(request.id, offset=0, limit=1)
    if current.status is AlbumRequestStatus.QUEUED:
        assert not (
            await service.toggle(
                request_id=request.id,
                item_id=items[0].id,
                telegram_user_id=9005,
            )
        ).accepted
        assert not (
            await service.open_quality(request_id=request.id, telegram_user_id=9005)
        ).accepted
        assert not (
            await service.download_all(request_id=request.id, telegram_user_id=9005)
        ).accepted


async def test_individual_and_album_share_singleflight(database: Database) -> None:
    async with database.transaction() as repositories:
        track = await repositories.tracks.create_track(title="Shared", artist="Artist")
        await repositories.track_sources.upsert_source(
            track_id=track.id,
            provider=MusicProviderName.SPOTIFY,
            provider_track_id="track-1",
            url="https://open.spotify.com/track/0123456789012345678901",
        )
    individual_user = await _user(database, 9007, QualityProfile.MP3_320)
    album_user = await _user(database, 9008, QualityProfile.MP3_320)

    @dataclass
    class IndividualResolver:
        async def resolve_track_id(self, url: str) -> int:
            return track.id

    track_service = TelegramTrackRequestService(database, IndividualResolver(), telegram_bot_id=100)
    individual = await track_service.request_track(
        user=individual_user,
        telegram_chat_id=9007,
        source_message_id=1,
        url="https://example.test/track",
    )
    assert (
        await track_service.start_default_quality(request_id=individual.id, telegram_user_id=9007)
    ).accepted

    album_service = _album_service(database, AlbumResolver(_snapshot(1)))
    album = await album_service.request_album(
        user=album_user,
        telegram_chat_id=9008,
        source_message_id=1,
        url="https://example.test/album",
    )
    assert (await album_service.download_all(request_id=album.id, telegram_user_id=9008)).accepted
    coordinator = _coordinator(database, TrackResolver(database), Gateway())
    assert await _drain_album(coordinator) == 1

    preparation = DeliveryPreparationService(database, telegram_bot_id=100, max_size=1000)
    async with database.transaction() as repositories:
        child = await repositories.telegram_delivery.get_by_album_item(1)
        assert child is not None and child.album_item_id is not None
    await preparation.prepare(
        track_id=individual.track_id,
        quality_profile=QualityProfile.MP3_320,
        request_key="tg:100:9007:1",
    )
    await preparation.prepare(
        track_id=child.track_id,
        quality_profile=QualityProfile.MP3_320,
        request_key=f"alb:{child.album_item_id}",
    )
    async with database.transaction() as repositories:
        session = repositories.singleflight._session  # noqa: SLF001
        assert await session.scalar(select(func.count(DownloadJob.id))) == 1
        assert await session.scalar(select(func.count(JobSubscriber.id))) == 2


async def test_mixed_cached_active_new_and_all_cached_album(database: Database) -> None:
    user = await _user(database, 9009, QualityProfile.MP3_320)
    service = _album_service(database, AlbumResolver(_snapshot(3)))
    request = await service.request_album(
        user=user,
        telegram_chat_id=9009,
        source_message_id=1,
        url="https://example.test/album",
    )
    assert (await service.download_all(request_id=request.id, telegram_user_id=9009)).accepted
    coordinator = _coordinator(database, TrackResolver(database), Gateway())
    assert await _drain_album(coordinator) == 3
    async with database.transaction() as repositories:
        session = repositories.telegram_album._session  # noqa: SLF001
        children = list(
            await session.scalars(
                select(TelegramDeliveryRequest).order_by(TelegramDeliveryRequest.id)
            )
        )
        sources = {
            child.track_id: (
                await repositories.track_sources.get_sources_for_track(child.track_id)
            )[0]
            for child in children
        }
    cache = TelegramFileCacheService(database)
    first = children[0]
    await cache.upsert_success(
        track_id=first.track_id,
        quality_profile=QualityProfile.MP3_320,
        receipt=TelegramUploadReceipt(
            100, -100, 1, TelegramMediaKind.AUDIO, "cached-a", "unique-a", 1234
        ),
        artifact=_artifact(sources[first.track_id]),
    )
    preparation = DeliveryPreparationService(database, telegram_bot_id=100, max_size=1000)
    await preparation.prepare(
        track_id=children[1].track_id,
        quality_profile=QualityProfile.MP3_320,
        request_key="individual-active",
    )
    results = []
    for child in children:
        assert child.album_item_id is not None
        results.append(
            await preparation.prepare(
                track_id=child.track_id,
                quality_profile=QualityProfile.MP3_320,
                request_key=f"alb:{child.album_item_id}",
            )
        )
    assert results[0].cached_file is not None
    assert results[1].subscriber is not None
    assert results[2].subscriber is not None
    async with database.transaction() as repositories:
        session = repositories.singleflight._session  # noqa: SLF001
        assert await session.scalar(select(func.count(DownloadJob.id))) == 2

    for index, child in enumerate(children[1:], start=2):
        await cache.upsert_success(
            track_id=child.track_id,
            quality_profile=QualityProfile.MP3_320,
            receipt=TelegramUploadReceipt(
                100,
                -100,
                index,
                TelegramMediaKind.AUDIO,
                f"cached-{index}",
                f"unique-{index}",
                1234,
            ),
            artifact=_artifact(sources[child.track_id]),
        )
    for index, child in enumerate(children, start=1):
        result = await preparation.prepare(
            track_id=child.track_id,
            quality_profile=QualityProfile.MP3_320,
            request_key=f"all-cached:{index}",
        )
        assert result.cached_file is not None
    async with database.transaction() as repositories:
        session = repositories.singleflight._session  # noqa: SLF001
        assert await session.scalar(select(func.count(DownloadJob.id))) == 2


async def test_quality_unavailable_child_isolated_in_partial_album(database: Database) -> None:
    user = await _user(database, 9010, QualityProfile.LOSSLESS)
    service = _album_service(database, AlbumResolver(_snapshot(3)))
    request = await service.request_album(
        user=user,
        telegram_chat_id=9010,
        source_message_id=1,
        url="https://example.test/album",
    )
    assert (await service.download_all(request_id=request.id, telegram_user_id=9010)).accepted
    coordinator = _coordinator(database, TrackResolver(database), Gateway())
    assert await _drain_album(coordinator) == 3
    async with database.transaction() as repositories:
        session = repositories.telegram_album._session  # noqa: SLF001
        children = list(
            await session.scalars(
                select(TelegramDeliveryRequest).order_by(TelegramDeliveryRequest.id)
            )
        )
        await session.execute(
            update(TelegramDeliveryRequest)
            .where(TelegramDeliveryRequest.id.in_([children[0].id, children[1].id]))
            .values(status=TelegramDeliveryStatus.DELIVERED, delivered_at=utc_now())
        )
        await session.execute(
            update(TelegramDeliveryRequest)
            .where(TelegramDeliveryRequest.id == children[2].id)
            .values(
                status=TelegramDeliveryStatus.FAILED,
                last_error_code="QUALITY_UNAVAILABLE",
            )
        )
    assert await coordinator.reconcile() == 1
    async with database.transaction() as repositories:
        terminal = await repositories.telegram_album.get(request.id)
        assert terminal is not None
        assert terminal.status is AlbumRequestStatus.PARTIALLY_FAILED


@pytest.mark.parametrize(
    ("delivery_status", "expected"),
    [
        (TelegramDeliveryStatus.DELIVERED, AlbumRequestStatus.COMPLETED),
        (TelegramDeliveryStatus.FAILED, AlbumRequestStatus.FAILED),
    ],
)
async def test_completion_reconciliation_all_deliver_or_all_fail(
    database: Database,
    delivery_status: TelegramDeliveryStatus,
    expected: AlbumRequestStatus,
) -> None:
    telegram_id = 9011 if delivery_status is TelegramDeliveryStatus.DELIVERED else 9012
    user = await _user(database, telegram_id, QualityProfile.MP3_320)
    service = _album_service(database, AlbumResolver(_snapshot(2)))
    request = await service.request_album(
        user=user,
        telegram_chat_id=telegram_id,
        source_message_id=1,
        url="https://example.test/album",
    )
    assert (
        await service.download_all(request_id=request.id, telegram_user_id=telegram_id)
    ).accepted
    gateway = Gateway()
    coordinator = _coordinator(database, TrackResolver(database), gateway)
    assert await _drain_album(coordinator) == 2
    async with database.transaction() as repositories:
        session = repositories.telegram_album._session  # noqa: SLF001
        values: dict[str, object] = {"status": delivery_status}
        if delivery_status is TelegramDeliveryStatus.DELIVERED:
            values["delivered_at"] = utc_now()
        else:
            values["last_error_code"] = "QUALITY_UNAVAILABLE"
        await session.execute(update(TelegramDeliveryRequest).values(**values))
    assert await coordinator.reconcile() == 1
    assert await coordinator.reconcile() == 0
    async with database.transaction() as repositories:
        terminal = await repositories.telegram_album.get(request.id)
        assert terminal is not None and terminal.status is expected
    assert len(gateway.summaries) == 1


async def test_100_user_same_album_fanout_and_second_wave_cache_reuse(
    database: Database,
) -> None:
    snapshot = _snapshot(5)
    resolver = TrackResolver(database)
    gateway = Gateway()
    coordinator = _coordinator(database, resolver, gateway)
    first_wave: list[TelegramAlbumRequest] = []
    for index in range(100):
        user = await _user(database, 10_000 + index, QualityProfile.MP3_320)
        service = _album_service(database, AlbumResolver(snapshot))
        request = await service.request_album(
            user=user,
            telegram_chat_id=user.telegram_id,
            source_message_id=1,
            url="https://example.test/album",
        )
        assert (
            await service.download_all(request_id=request.id, telegram_user_id=user.telegram_id)
        ).accepted
        first_wave.append(request)
    assert await _drain_album(coordinator) == 500

    preparation = DeliveryPreparationService(database, telegram_bot_id=100, max_size=1000)
    async with database.transaction() as repositories:
        session = repositories.telegram_album._session  # noqa: SLF001
        first_children = list(
            await session.scalars(
                select(TelegramDeliveryRequest).order_by(TelegramDeliveryRequest.id)
            )
        )
    for child in first_children:
        assert child.quality_profile is not None and child.album_item_id is not None
        await preparation.prepare(
            track_id=child.track_id,
            quality_profile=child.quality_profile,
            request_key=f"alb:{child.album_item_id}",
        )
    async with database.transaction() as repositories:
        session = repositories.singleflight._session  # noqa: SLF001
        assert await session.scalar(select(func.count(DownloadJob.id))) == 5
        assert await session.scalar(select(func.count(JobSubscriber.id))) == 500
        assert await session.scalar(select(func.count(TelegramDeliveryRequest.id))) == 500

        tracks = list(await session.scalars(select(TelegramAlbumItem.track_id).distinct()))
        sources = {
            source.track_id: source
            for track_id in tracks
            if track_id is not None
            for source in await repositories.track_sources.get_sources_for_track(track_id)
        }
    cache = TelegramFileCacheService(database)
    for track_id, source in sources.items():
        await cache.upsert_success(
            track_id=track_id,
            quality_profile=QualityProfile.MP3_320,
            receipt=TelegramUploadReceipt(
                100,
                -100,
                20_000 + track_id,
                TelegramMediaKind.AUDIO,
                f"file-{track_id}",
                f"unique-{track_id}",
                1234,
            ),
            artifact=DownloadArtifactMetadata(
                source.id,
                source.provider,
                source.provider_track_id,
                DownloadPlanOperation.TRANSCODE,
                True,
                NativeCodec.FLAC,
                NativeContainer.FLAC,
                None,
                NativeCodec.MP3,
                NativeContainer.MP3,
                320,
                None,
                None,
                None,
                180_000,
                1234,
                "libmp3lame",
            ),
        )

    second_children: list[TelegramDeliveryRequest] = []
    for index in range(100):
        user = await _user(database, 20_000 + index, QualityProfile.MP3_320)
        service = _album_service(database, AlbumResolver(snapshot))
        request = await service.request_album(
            user=user,
            telegram_chat_id=user.telegram_id,
            source_message_id=1,
            url="https://example.test/album",
        )
        assert (
            await service.download_all(request_id=request.id, telegram_user_id=user.telegram_id)
        ).accepted
    assert await _drain_album(coordinator) == 500
    async with database.transaction() as repositories:
        session = repositories.telegram_album._session  # noqa: SLF001
        second_children = list(
            await session.scalars(
                select(TelegramDeliveryRequest)
                .where(TelegramDeliveryRequest.id > first_children[-1].id)
                .order_by(TelegramDeliveryRequest.id)
            )
        )
    assert len(second_children) == 500
    for child in second_children:
        assert child.quality_profile is not None and child.album_item_id is not None
        result = await preparation.prepare(
            track_id=child.track_id,
            quality_profile=child.quality_profile,
            request_key=f"alb:{child.album_item_id}",
        )
        assert result.cached_file is not None
    async with database.transaction() as repositories:
        session = repositories.singleflight._session  # noqa: SLF001
        assert await session.scalar(select(func.count(DownloadJob.id))) == 5
        assert await session.scalar(select(func.count(JobSubscriber.id))) == 500
        assert await session.scalar(select(func.count(UploadJob.id))) == 0
        assert await session.scalar(select(func.count(TelegramDeliveryRequest.id))) == 1000
