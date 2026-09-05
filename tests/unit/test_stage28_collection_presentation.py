from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.download_preferences import UserDownloadPreferences
from app.core.enums import BatchItemStatus, BatchSourceType, BatchStatus, MusicProviderName
from app.core.models import ResolvedCollection, ResolvedCollectionItem
from app.services.batch_download import BatchDownloadService
from app.services.collection_status_presentation import CollectionStatusPresentationService
from app.telegram import TelegramDeliveryReceipt, TelegramGatewayError
from app.telegram.ux_presentation import TelegramStatusUpdatePolicy


@dataclass
class _Resolver:
    collection: ResolvedCollection

    async def resolve_collection(
        self, source_type: BatchSourceType, source_reference: str
    ) -> ResolvedCollection:
        return self.collection


class _Gateway:
    def __init__(self) -> None:
        self.edits: list[tuple[int, int, str]] = []
        self.sent: list[tuple[int, str]] = []
        self.edit_error: TelegramGatewayError | None = None

    async def edit_text(self, chat_id: int, message_id: int, text: str) -> None:
        self.edits.append((chat_id, message_id, text))
        if self.edit_error is not None:
            raise self.edit_error

    async def send_text(self, chat_id: int, text: str) -> TelegramDeliveryReceipt:
        self.sent.append((chat_id, text))
        return TelegramDeliveryReceipt(chat_id=chat_id, message_id=900 + len(self.sent))


def _collection(*item_ids: str) -> ResolvedCollection:
    return ResolvedCollection(
        source_type=BatchSourceType.ALBUM,
        provider=MusicProviderName.SPOTIFY,
        collection_id="stage28-album",
        source_reference="https://example.invalid/album/stage28",
        title="Stage 28 Album",
        creator="Artist",
        items=tuple(
            ResolvedCollectionItem(position=index, provider_media_id=item_id, title=item_id)
            for index, item_id in enumerate(item_ids, 1)
        ),
    )


async def _batch(
    database,  # type: ignore[no-untyped-def]
    *,
    confirmation_id: str,
    message_id: int,
    item_ids: tuple[str, ...] = ("a", "b", "c"),
):
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(280_100 + message_id)
    batches = BatchDownloadService(database, _Resolver(_collection(*item_ids)))
    batch = await batches.expand(
        user_id=user.id,
        confirmation_id=confirmation_id,
        source_type=BatchSourceType.ALBUM,
        source_reference="stage28",
        preferences=UserDownloadPreferences(user.id),
    )
    assert await batches.record_parent_message(
        batch_id=batch.id,
        user_id=user.id,
        bot_id=28,
        chat_id=user.telegram_id,
        message_id=message_id,
    )
    return batches, batch, user


def _presentation(database, batches, gateway, now):  # type: ignore[no-untyped-def]
    return CollectionStatusPresentationService(
        database,
        batches,
        gateway,
        update_policy=TelegramStatusUpdatePolicy(clock=lambda: now[0]),
    )


@pytest.mark.asyncio
async def test_live_batch_reconcile_coalesces_parent_edits(database) -> None:  # type: ignore[no-untyped-def]
    now = [datetime(2026, 9, 2, tzinfo=UTC)]
    batches, batch, _ = await _batch(database, confirmation_id="stage28-live", message_id=1)
    gateway = _Gateway()
    presentation = _presentation(database, batches, gateway, now)
    batches.set_presentation_observer(presentation.refresh)

    assert await batches.reconcile(batch.id) is BatchStatus.ACTIVE
    async with database.transaction() as repositories:
        items = await repositories.batch_download.list_items(batch.id)
        await repositories.batch_download.set_item(
            items[0].id, status=BatchItemStatus.FAILED, now=now[0]
        )
    assert await batches.reconcile(batch.id) is BatchStatus.ACTIVE
    async with database.transaction() as repositories:
        items = await repositories.batch_download.list_items(batch.id)
        await repositories.batch_download.set_item(
            items[1].id, status=BatchItemStatus.FAILED, now=now[0]
        )
    assert await batches.reconcile(batch.id) is BatchStatus.ACTIVE

    assert len(gateway.edits) == 1
    assert gateway.sent == []
    now[0] += timedelta(seconds=3)
    assert await batches.reconcile(batch.id) is BatchStatus.ACTIVE
    assert len(gateway.edits) == 2
    assert "2 unavailable" in gateway.edits[-1][2]


@pytest.mark.asyncio
async def test_terminal_collection_bypasses_active_throttle(database) -> None:  # type: ignore[no-untyped-def]
    now = [datetime(2026, 9, 2, tzinfo=UTC)]
    batches, batch, user = await _batch(
        database, confirmation_id="stage28-terminal", message_id=2, item_ids=("a",)
    )
    gateway = _Gateway()
    presentation = _presentation(database, batches, gateway, now)
    batches.set_presentation_observer(presentation.refresh)

    assert await batches.reconcile(batch.id) is BatchStatus.ACTIVE
    assert await batches.cancel(batch.id, user_id=user.id)
    assert await batches.reconcile(batch.id) is BatchStatus.CANCELLED

    assert len(gateway.edits) == 2
    assert "Album download cancelled" in gateway.edits[-1][2]


@pytest.mark.asyncio
async def test_terminal_parent_replacement_is_persisted_and_reused(database) -> None:  # type: ignore[no-untyped-def]
    now = [datetime(2026, 9, 2, tzinfo=UTC)]
    batches, batch, _ = await _batch(
        database, confirmation_id="stage28-replace", message_id=3, item_ids=("a",)
    )
    gateway = _Gateway()
    presentation = _presentation(database, batches, gateway, now)
    async with database.transaction() as repositories:
        await repositories.batch_download.mark_status(batch.id, BatchStatus.COMPLETED, now[0])

    gateway.edit_error = TelegramGatewayError("MESSAGE_NOT_FOUND", retryable=False)
    await presentation.refresh(batch.id)
    assert len(gateway.sent) == 1
    async with database.transaction() as repositories:
        replaced = await repositories.batch_download.get(batch.id)
    assert replaced is not None
    assert replaced.parent_message_id == 901

    gateway.edit_error = None
    await presentation.refresh(batch.id)
    assert len(gateway.sent) == 1
    assert gateway.edits[-1][1] == 901


@pytest.mark.asyncio
async def test_collection_throttle_is_independent_per_parent_batch(database) -> None:  # type: ignore[no-untyped-def]
    now = [datetime(2026, 9, 2, tzinfo=UTC)]
    batches_a, batch_a, _ = await _batch(database, confirmation_id="stage28-a", message_id=4)
    batches_b, batch_b, _ = await _batch(database, confirmation_id="stage28-b", message_id=5)
    gateway = _Gateway()
    policy = TelegramStatusUpdatePolicy(clock=lambda: now[0])
    presentation_a = CollectionStatusPresentationService(
        database, batches_a, gateway, update_policy=policy
    )
    presentation_b = CollectionStatusPresentationService(
        database, batches_b, gateway, update_policy=policy
    )

    await presentation_a.refresh(batch_a.id)
    await presentation_b.refresh(batch_b.id)

    assert [edit[1] for edit in gateway.edits] == [4, 5]


@pytest.mark.asyncio
async def test_live_presentation_failure_does_not_rollback_batch_reconciliation(database) -> None:  # type: ignore[no-untyped-def]
    now = [datetime(2026, 9, 2, tzinfo=UTC)]
    batches, batch, _ = await _batch(database, confirmation_id="stage28-isolate", message_id=6)
    gateway = _Gateway()
    gateway.edit_error = TelegramGatewayError("TEMPORARY", retryable=True)
    presentation = _presentation(database, batches, gateway, now)
    batches.set_presentation_observer(presentation.refresh)

    assert await batches.reconcile(batch.id) is BatchStatus.ACTIVE
    async with database.transaction() as repositories:
        persisted = await repositories.batch_download.get(batch.id)
    assert persisted is not None
    assert persisted.status is BatchStatus.ACTIVE


@pytest.mark.asyncio
async def test_restart_reconciliation_reuses_live_parent_message_without_spam(database) -> None:  # type: ignore[no-untyped-def]
    now = [datetime(2026, 9, 2, tzinfo=UTC)]
    batches, batch, _ = await _batch(database, confirmation_id="stage28-restart", message_id=7)
    gateway = _Gateway()
    live_presentation = _presentation(database, batches, gateway, now)
    batches.set_presentation_observer(live_presentation.refresh)
    assert await batches.reconcile(batch.id) is BatchStatus.ACTIVE

    async def resume_child(*args, **kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(request_id=None)

    restarted_batches = BatchDownloadService(
        database, _Resolver(_collection("a", "b", "c")), child_admitter=resume_child
    )
    restarted_presentation = _presentation(database, restarted_batches, gateway, now)
    restarted_batches.set_presentation_observer(restarted_presentation.refresh)
    assert await restarted_batches.reconcile_all() == 1
    assert await restarted_presentation.reconcile_startup() == 1

    async with database.transaction() as repositories:
        durable = await repositories.batch_download.get_by_confirmation("stage28-restart")
        items = await repositories.batch_download.list_items(batch.id)
    assert durable is not None
    assert durable.id == batch.id
    assert len(items) == 3
    assert len(gateway.edits) == 2
    assert gateway.sent == []
