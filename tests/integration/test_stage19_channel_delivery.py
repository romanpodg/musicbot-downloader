from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.application.download import DownloadService, DownloadTrackUseCase
from app.core.download import DownloadDeliveryTarget
from app.core.enums import (
    DownloadPlanOperation,
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    QualityProfile,
    QueueErrorCode,
    TelegramDeliveryStatus,
    TelegramMediaKind,
)
from app.core.models import DownloadArtifactMetadata, TelegramBotIdentity, TelegramUploadReceipt
from app.core.recognition import RecognitionDecision, RecognitionResult, TrackCandidate
from app.core.search import Artist, Track
from app.core.telegram_context import (
    ChannelBinding,
    ChannelBindingStatus,
    ChatPolicy,
    DeliveryMode,
    TelegramChatType,
    TelegramContext,
)
from app.i18n import LocalizationService
from app.services.delivery import DeliveryPreparationService
from app.services.download_requests import ExistingDeliverySubmissionService
from app.services.telegram_cache import TelegramFileCacheService
from app.services.telegram_context import ChatContextAccessService, DeliveryTargetResolver
from app.services.telegram_delivery import TelegramDeliveryWorker
from app.services.telegram_requests import TelegramTrackRequestService
from app.storage import Database
from app.telegram import (
    TelegramCachedMediaSpec,
    TelegramDeliveryReceipt,
    TelegramGatewayError,
    TelegramUploadSpec,
)


@dataclass
class _CanonicalResolver:
    track_id: int

    async def resolve_track_id(self, track: Track) -> int:
        return self.track_id


class _UnusedUrlResolver:
    async def resolve_track_id(self, url: str) -> int:
        raise AssertionError("Stage 19 admission must use the canonical recognized track")


class _Gateway:
    def __init__(self) -> None:
        self.sent_to: list[int] = []
        self.texts: list[tuple[int, str]] = []
        self.permission_denied = False
        self.denied_chat_ids: set[int] = set()

    async def get_bot_identity(self) -> TelegramBotIdentity:
        return TelegramBotIdentity(190, "stage19_bot")

    async def upload_audio(self, spec: TelegramUploadSpec) -> TelegramUploadReceipt:
        raise AssertionError("the integration test starts with an existing cache entry")

    async def upload_document(self, spec: TelegramUploadSpec) -> TelegramUploadReceipt:
        raise AssertionError("the integration test starts with an existing cache entry")

    async def send_cached_audio(self, spec: TelegramCachedMediaSpec) -> TelegramDeliveryReceipt:
        if self.permission_denied and (spec.chat_id > 0 or spec.chat_id in self.denied_chat_ids):
            raise TelegramGatewayError(
                QueueErrorCode.TELEGRAM_PERMISSION_DENIED.value, retryable=False
            )
        self.sent_to.append(spec.chat_id)
        return TelegramDeliveryReceipt(spec.chat_id, len(self.sent_to))

    async def send_cached_document(self, spec: TelegramCachedMediaSpec) -> TelegramDeliveryReceipt:
        return await self.send_cached_audio(spec)

    async def send_text(self, chat_id: int, text: str) -> TelegramDeliveryReceipt:
        self.texts.append((chat_id, text))
        return TelegramDeliveryReceipt(chat_id, 1)

    async def can_send_messages(self, chat_id: int) -> bool:
        return True

    async def close(self) -> None:
        return None


async def _track_and_cache(database: Database) -> int:
    async with database.transaction() as repositories:
        track = await repositories.tracks.create_track(title="Stage 19", artist="Music Bot")
        source = await repositories.track_sources.upsert_source(
            track_id=track.id,
            provider=MusicProviderName.QOBUZ,
            provider_track_id="stage19-source",
            url="https://example.test/stage19",
        )
    metadata = DownloadArtifactMetadata(
        source.source.id,
        MusicProviderName.QOBUZ,
        "stage19-source",
        DownloadPlanOperation.DIRECT,
        False,
        NativeCodec.MP3,
        NativeContainer.MP3,
        320,
        NativeCodec.MP3,
        NativeContainer.MP3,
        320,
        48000,
        None,
        2,
        180000,
        12,
        "native",
    )
    await TelegramFileCacheService(database).upsert_success(
        track_id=track.id,
        quality_profile=QualityProfile.MP3_320,
        receipt=TelegramUploadReceipt(
            190,
            -100190,
            1,
            TelegramMediaKind.AUDIO,
            "stage19-file-id",
            "stage19-unique-id",
            12,
        ),
        artifact=metadata,
    )
    return track.id


async def _deliver(
    database: Database,
    gateway: _Gateway,
    context: TelegramContext,
    source_message_id: int,
    track_id: int,
    *,
    expect_delivered: bool = True,
) -> TelegramDeliveryStatus:
    async with database.transaction() as repositories:
        user = await repositories.users.get_by_telegram_id(context.user_id)
    assert user is not None
    access = await ChatContextAccessService(database, DeliveryTargetResolver(), gateway).resolve(
        context, user
    )
    assert access.allowed and access.target is not None
    catalog_track = Track(
        id="search:spotify:stage19",
        title="Stage 19",
        artists=(Artist("Music Bot"),),
        provider=MusicProviderName.SPOTIFY,
        provider_track_id="stage19",
    )
    recognition = RecognitionResult(
        TrackCandidate(catalog_track, "spotify"), 0.96, RecognitionDecision.ACCEPT
    )
    downloads = DownloadService(
        DownloadTrackUseCase(
            _CanonicalResolver(track_id),
            ExistingDeliverySubmissionService(
                database,
                TelegramTrackRequestService(database, _UnusedUrlResolver(), telegram_bot_id=190),
            ),
        ),
        token_factory=lambda: f"{source_message_id:024x}",
    )
    confirmation = downloads.create_confirmation(context=context, result=recognition)
    assert confirmation is not None
    submission = await downloads.confirm(
        context=context,
        token=confirmation.token,
        target=DownloadDeliveryTarget(context.user_id, context, access.target, source_message_id),
    )
    assert submission is not None
    worker = TelegramDeliveryWorker(
        database,
        DeliveryPreparationService(database, telegram_bot_id=190, max_size=10),
        TelegramFileCacheService(database),
        gateway,
        LocalizationService(("en", "ru"), "en"),
        max_attempts=3,
        wake_event=asyncio.Event(),
    )
    pending = await worker.claim(f"stage19-{source_message_id}-prepare")
    assert pending is not None
    await worker.process(pending, f"stage19-{source_message_id}-prepare")
    ready = await worker.claim(f"stage19-{source_message_id}-send")
    assert ready is not None
    await worker.process(ready, f"stage19-{source_message_id}-send")
    async with database.transaction() as repositories:
        stored = await repositories.telegram_delivery.get(submission.delivery_request_id)
    assert stored is not None
    if expect_delivered:
        assert stored.status is TelegramDeliveryStatus.DELIVERED
    return stored.status


async def test_stage19_private_group_and_bound_channel_delivery(database: Database) -> None:
    track_id = await _track_and_cache(database)
    user_id = 19001
    async with database.transaction() as repositories:
        await repositories.users.create_user(
            user_id, preferred_quality_profile=QualityProfile.MP3_320
        )
        await repositories.telegram_context.upsert_chat_policy(
            ChatPolicy(-19002, True, DeliveryMode.CHAT)
        )
        await repositories.telegram_context.upsert_chat_policy(
            ChatPolicy(-10019003, True, DeliveryMode.CHAT)
        )
        await repositories.telegram_context.upsert_channel_binding(
            ChannelBinding(-10019003, ChannelBindingStatus.CONNECTED)
        )
    gateway = _Gateway()
    await _deliver(
        database,
        gateway,
        TelegramContext(user_id, user_id, TelegramChatType.PRIVATE),
        1901,
        track_id,
    )
    await _deliver(
        database,
        gateway,
        TelegramContext(user_id, -19002, TelegramChatType.GROUP),
        1902,
        track_id,
    )
    await _deliver(
        database,
        gateway,
        TelegramContext(user_id, -10019003, TelegramChatType.CHANNEL),
        1903,
        track_id,
    )
    assert gateway.sent_to == [user_id, -19002, -10019003]


async def test_stage20_group_user_delivery_permission_failure_is_terminal_and_notifies_origin(
    database: Database,
) -> None:
    track_id = await _track_and_cache(database)
    user_id = 19011
    group_id = -19011
    async with database.transaction() as repositories:
        await repositories.users.create_user(
            user_id, preferred_quality_profile=QualityProfile.MP3_320
        )
        await repositories.telegram_context.upsert_chat_policy(
            ChatPolicy(group_id, True, DeliveryMode.USER)
        )
    gateway = _Gateway()
    gateway.permission_denied = True
    status = await _deliver(
        database,
        gateway,
        TelegramContext(user_id, group_id, TelegramChatType.GROUP),
        1915,
        track_id,
        expect_delivered=False,
    )

    assert status is TelegramDeliveryStatus.FAILED
    assert [chat_id for chat_id, _ in gateway.texts] == [group_id]
    assert "/start" in gateway.texts[0][1]


async def test_stage20_channel_permission_failure_has_no_private_start_guidance(
    database: Database,
) -> None:
    track_id = await _track_and_cache(database)
    user_id = 19012
    channel_id = -10019012
    async with database.transaction() as repositories:
        await repositories.users.create_user(
            user_id, preferred_quality_profile=QualityProfile.MP3_320
        )
        await repositories.telegram_context.upsert_chat_policy(
            ChatPolicy(channel_id, True, DeliveryMode.CHAT)
        )
        await repositories.telegram_context.upsert_channel_binding(
            ChannelBinding(channel_id, ChannelBindingStatus.CONNECTED)
        )
    gateway = _Gateway()
    gateway.permission_denied = True
    gateway.denied_chat_ids.add(channel_id)
    status = await _deliver(
        database,
        gateway,
        TelegramContext(user_id, channel_id, TelegramChatType.CHANNEL),
        1916,
        track_id,
        expect_delivered=False,
    )

    assert status is TelegramDeliveryStatus.FAILED
    assert gateway.texts == []
