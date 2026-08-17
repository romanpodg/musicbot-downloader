"""Stage 9 track-request admission and first-quality continuation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from app.core.enums import QualityProfile, TelegramDeliveryStatus
from app.core.exceptions import DatabaseConcurrencyError
from app.providers.base import MusicProvider
from app.services.track_resolution import ResolveTrackService
from app.storage import Database
from app.storage.models import TelegramDeliveryRequest, User
from app.storage.models.base import utc_now


class TelegramTrackResolver(Protocol):
    async def resolve_track_id(self, url: str) -> int: ...


class ResolveTrackAdapter:
    def __init__(
        self,
        service: ResolveTrackService,
        *,
        database: Database | None = None,
        provider: MusicProvider | None = None,
    ) -> None:
        self._service = service
        self._database = database
        self._provider = provider

    async def resolve_track_id(self, url: str) -> int:
        if self._database is not None and self._provider is not None:
            reference = self._provider.detect_url(url)
            async with self._database.transaction() as repositories:
                source = await repositories.track_sources.get_source(
                    reference.provider, reference.provider_track_id
                )
                if source is not None:
                    return source.track_id
        result = await self._service.resolve(url, discover=True)
        return result.track.id


@dataclass(frozen=True, slots=True)
class QualitySelectionResult:
    request: TelegramDeliveryRequest | None
    accepted: bool


class TelegramTrackRequestService:
    def __init__(
        self,
        database: Database,
        resolver: TelegramTrackResolver,
        *,
        telegram_bot_id: int,
        wake_event: asyncio.Event | None = None,
    ) -> None:
        self._database = database
        self._resolver = resolver
        self._telegram_bot_id = telegram_bot_id
        self._wake_event = wake_event

    async def request_track(
        self,
        *,
        user: User,
        telegram_chat_id: int,
        source_message_id: int,
        url: str,
    ) -> TelegramDeliveryRequest:
        existing = await self._get_by_message(telegram_chat_id, source_message_id)
        if existing is not None:
            return existing

        track_id = await self._resolver.resolve_track_id(url)
        try:
            async with self._database.transaction() as repositories:
                existing = await repositories.telegram_delivery.get_by_message(
                    telegram_bot_id=self._telegram_bot_id,
                    telegram_chat_id=telegram_chat_id,
                    source_message_id=source_message_id,
                )
                if existing is not None:
                    return existing
                quality = user.preferred_quality_profile
                request = await repositories.telegram_delivery.create(
                    telegram_bot_id=self._telegram_bot_id,
                    user_id=user.id,
                    telegram_chat_id=telegram_chat_id,
                    source_message_id=source_message_id,
                    track_id=track_id,
                    quality_profile=quality,
                    status=(
                        TelegramDeliveryStatus.AWAITING_QUALITY
                        if quality is None
                        else TelegramDeliveryStatus.QUEUED
                    ),
                    now=utc_now(),
                )
        except DatabaseConcurrencyError:
            recovered = await self._get_by_message(telegram_chat_id, source_message_id)
            if recovered is None:
                raise
            request = recovered
        if request.status is TelegramDeliveryStatus.QUEUED:
            self.wake()
        return request

    async def choose_first_quality(
        self,
        *,
        request_id: int,
        telegram_user_id: int,
        quality_profile: QualityProfile,
    ) -> QualitySelectionResult:
        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(telegram_user_id)
            if user is None:
                return QualitySelectionResult(None, False)
            request = await repositories.telegram_delivery.choose_quality(
                request_id=request_id,
                user_id=user.id,
                quality_profile=quality_profile,
                now=utc_now(),
            )
            if request is None:
                return QualitySelectionResult(
                    await repositories.telegram_delivery.get(request_id), False
                )
            await repositories.users.set_preferred_quality(user, quality_profile)
        self.wake()
        return QualitySelectionResult(request, True)

    async def _get_by_message(
        self, telegram_chat_id: int, source_message_id: int
    ) -> TelegramDeliveryRequest | None:
        async with self._database.transaction() as repositories:
            return await repositories.telegram_delivery.get_by_message(
                telegram_bot_id=self._telegram_bot_id,
                telegram_chat_id=telegram_chat_id,
                source_message_id=source_message_id,
            )

    def wake(self) -> None:
        if self._wake_event is not None:
            self._wake_event.set()
