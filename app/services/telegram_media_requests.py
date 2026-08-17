"""Provider-neutral Track-versus-Album Telegram input routing."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.exceptions import (
    AlbumResolutionFailed,
    MetadataUnavailable,
    ProviderAuthenticationError,
    ProviderUnavailable,
)
from app.providers.base import AlbumReference, MusicProvider, TrackReference
from app.services.telegram_albums import TelegramAlbumRequestService
from app.services.telegram_requests import TelegramTrackRequestService
from app.storage.models import TelegramAlbumRequest, TelegramDeliveryRequest, User


@dataclass(frozen=True, slots=True)
class MediaAdmission:
    track: TelegramDeliveryRequest | None = None
    album: TelegramAlbumRequest | None = None


class TelegramMediaRequestService:
    def __init__(
        self,
        provider: MusicProvider,
        tracks: TelegramTrackRequestService,
        albums: TelegramAlbumRequestService,
    ) -> None:
        self._provider = provider
        self._tracks = tracks
        self._albums = albums

    async def request(
        self,
        *,
        user: User,
        telegram_chat_id: int,
        source_message_id: int,
        url: str,
    ) -> MediaAdmission:
        reference = await self._provider.classify_url(url)
        if isinstance(reference, TrackReference):
            return MediaAdmission(
                track=await self._tracks.request_track(
                    user=user,
                    telegram_chat_id=telegram_chat_id,
                    source_message_id=source_message_id,
                    url=reference.source_url,
                )
            )
        if isinstance(reference, AlbumReference):
            try:
                return MediaAdmission(
                    album=await self._albums.request_album(
                        user=user,
                        telegram_chat_id=telegram_chat_id,
                        source_message_id=source_message_id,
                        url=reference.source_url,
                    )
                )
            except (
                MetadataUnavailable,
                ProviderAuthenticationError,
                ProviderUnavailable,
            ) as exc:
                raise AlbumResolutionFailed() from exc
        raise TypeError("unsupported media reference")
