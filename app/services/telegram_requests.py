"""Durable Stage 9 track-request admission and interactive actions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.core.delivery_targets import DeliveryTarget
from app.core.enums import QualityProfile, TelegramDeliveryStatus
from app.core.exceptions import DatabaseConcurrencyError
from app.providers.base import MusicProvider
from app.services.track_resolution import ResolveTrackService
from app.storage import Database
from app.storage.models import TelegramDeliveryRequest, Track, User
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


class TrackRequestActionOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    STALE = "STALE"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True, slots=True)
class TrackRequestActionResult:
    request: TelegramDeliveryRequest | None
    outcome: TrackRequestActionOutcome

    @property
    def accepted(self) -> bool:
        return self.outcome is TrackRequestActionOutcome.ACCEPTED


@dataclass(frozen=True, slots=True)
class TrackCard:
    request_id: int
    status: TelegramDeliveryStatus
    quality_profile: QualityProfile | None
    artist: str | None
    title: str | None
    album: str | None
    duration_ms: int | None
    card_message_id: int | None


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
        delivery_target: DeliveryTarget | None = None,
        source_message_id: int,
        url: str,
    ) -> TelegramDeliveryRequest:
        existing = await self._get_by_message(telegram_chat_id, source_message_id)
        if existing is not None:
            return existing

        track_id = await self._resolver.resolve_track_id(url)
        return await self.request_track_id(
            user=user,
            telegram_chat_id=telegram_chat_id,
            delivery_target=delivery_target,
            source_message_id=source_message_id,
            track_id=track_id,
        )

    async def request_track_id(
        self,
        *,
        user: User,
        telegram_chat_id: int,
        delivery_target: DeliveryTarget | None = None,
        source_message_id: int,
        track_id: int,
    ) -> TelegramDeliveryRequest:
        existing = await self._get_by_message(telegram_chat_id, source_message_id)
        if existing is not None:
            return existing
        try:
            async with self._database.transaction() as repositories:
                existing = await repositories.telegram_delivery.get_by_message(
                    telegram_bot_id=self._telegram_bot_id,
                    telegram_chat_id=telegram_chat_id,
                    source_message_id=source_message_id,
                )
                if existing is not None:
                    return existing
                stored_user = await repositories.users.get(user.id)
                if stored_user is None:
                    raise ValueError("Telegram user disappeared during request admission")
                if await repositories.tracks.get_track_by_id(track_id) is None:
                    raise ValueError("Deep-link Track target no longer exists")
                quality = stored_user.preferred_quality_profile
                request = await repositories.telegram_delivery.create(
                    telegram_bot_id=self._telegram_bot_id,
                    user_id=user.id,
                    telegram_chat_id=telegram_chat_id,
                    delivery_target=delivery_target,
                    source_message_id=source_message_id,
                    track_id=track_id,
                    quality_profile=quality,
                    status=(
                        TelegramDeliveryStatus.AWAITING_QUALITY
                        if quality is None
                        else TelegramDeliveryStatus.AWAITING_ACTION
                    ),
                    now=utc_now(),
                )
        except DatabaseConcurrencyError:
            recovered = await self._get_by_message(telegram_chat_id, source_message_id)
            if recovered is None:
                raise
            request = recovered
        return request

    async def choose_first_quality(
        self,
        *,
        request_id: int,
        telegram_user_id: int,
        telegram_chat_id: int | None = None,
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
                telegram_chat_id=telegram_chat_id,
            )
            if request is None:
                return QualitySelectionResult(
                    await repositories.telegram_delivery.get(request_id), False
                )
            await repositories.users.set_preferred_quality(user, quality_profile)
        self.wake()
        return QualitySelectionResult(request, True)

    async def track_card(
        self, *, request_id: int, telegram_user_id: int, telegram_chat_id: int | None = None
    ) -> TrackCard | None:
        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(telegram_user_id)
            request = await repositories.telegram_delivery.get(request_id)
            if (
                user is None
                or request is None
                or request.user_id != user.id
                or (telegram_chat_id is not None and request.telegram_chat_id != telegram_chat_id)
            ):
                return None
            track = await repositories.tracks.get_track_by_id(request.track_id)
            if track is None:
                return None
            return _track_card(request, track)

    async def record_card_message(
        self,
        *,
        request_id: int,
        telegram_user_id: int,
        message_id: int,
        telegram_chat_id: int | None = None,
    ) -> bool:
        if message_id <= 0:
            return False
        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(telegram_user_id)
            if user is None:
                return False
            return await repositories.telegram_delivery.record_card_message(
                request_id=request_id,
                user_id=user.id,
                message_id=message_id,
                telegram_chat_id=telegram_chat_id,
            )

    async def start_default_quality(
        self, *, request_id: int, telegram_user_id: int, telegram_chat_id: int | None = None
    ) -> TrackRequestActionResult:
        return await self._perform_action(
            request_id=request_id,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            action="start_default",
            wake=True,
        )

    async def open_track_quality(
        self, *, request_id: int, telegram_user_id: int, telegram_chat_id: int | None = None
    ) -> TrackRequestActionResult:
        return await self._perform_action(
            request_id=request_id,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            action="open_quality",
        )

    async def choose_track_quality(
        self,
        *,
        request_id: int,
        telegram_user_id: int,
        telegram_chat_id: int | None = None,
        quality_profile: QualityProfile,
    ) -> TrackRequestActionResult:
        return await self._perform_action(
            request_id=request_id,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            action="choose_quality",
            quality_profile=quality_profile,
            wake=True,
        )

    async def back_to_track_card(
        self, *, request_id: int, telegram_user_id: int, telegram_chat_id: int | None = None
    ) -> TrackRequestActionResult:
        return await self._perform_action(
            request_id=request_id,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            action="back",
        )

    async def _perform_action(
        self,
        *,
        request_id: int,
        telegram_user_id: int,
        telegram_chat_id: int | None = None,
        action: str,
        quality_profile: QualityProfile | None = None,
        wake: bool = False,
    ) -> TrackRequestActionResult:
        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(telegram_user_id)
            if user is None:
                return TrackRequestActionResult(None, TrackRequestActionOutcome.NOT_FOUND)
            now = utc_now()
            if action == "start_default":
                changed = await repositories.telegram_delivery.start_default_quality(
                    request_id=request_id,
                    user_id=user.id,
                    now=now,
                    telegram_chat_id=telegram_chat_id,
                )
            elif action == "open_quality":
                changed = await repositories.telegram_delivery.open_track_quality(
                    request_id=request_id,
                    user_id=user.id,
                    now=now,
                    telegram_chat_id=telegram_chat_id,
                )
            elif action == "choose_quality" and quality_profile is not None:
                changed = await repositories.telegram_delivery.choose_track_quality(
                    request_id=request_id,
                    user_id=user.id,
                    quality_profile=quality_profile,
                    now=now,
                    telegram_chat_id=telegram_chat_id,
                )
            elif action == "back":
                changed = await repositories.telegram_delivery.back_to_action(
                    request_id=request_id,
                    user_id=user.id,
                    now=now,
                    telegram_chat_id=telegram_chat_id,
                )
            else:
                raise ValueError("invalid track request action")
            if changed is not None:
                result = TrackRequestActionResult(changed, TrackRequestActionOutcome.ACCEPTED)
            else:
                current = await repositories.telegram_delivery.get(request_id)
                if current is None:
                    outcome = TrackRequestActionOutcome.NOT_FOUND
                elif current.user_id != user.id or (
                    telegram_chat_id is not None and current.telegram_chat_id != telegram_chat_id
                ):
                    outcome = TrackRequestActionOutcome.FORBIDDEN
                else:
                    outcome = TrackRequestActionOutcome.STALE
                result = TrackRequestActionResult(current, outcome)
        if result.accepted and wake:
            self.wake()
        return result

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


def _track_card(request: TelegramDeliveryRequest, track: Track) -> TrackCard:
    return TrackCard(
        request_id=request.id,
        status=request.status,
        quality_profile=request.quality_profile,
        artist=track.artist,
        title=track.title,
        album=track.album,
        duration_ms=track.duration_ms,
        card_message_id=request.card_message_id,
    )
