"""Provider-neutral Track-versus-Album Telegram input routing."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.core.enums import BatchSourceType
from app.core.exceptions import (
    AlbumResolutionFailed,
    MetadataUnavailable,
    ProviderAuthenticationError,
    ProviderUnavailable,
    UnsupportedAlbum,
    UnsupportedMediaType,
)
from app.providers.base import AlbumReference, MusicProvider, PlaylistReference, TrackReference
from app.services.batch_download import BatchDownloadService
from app.services.download_preferences import UserDownloadPreferencesService
from app.services.telegram_albums import TelegramAlbumRequestService
from app.services.telegram_requests import TelegramTrackRequestService
from app.storage.models import (
    BatchDownloadRequest,
    TelegramAlbumRequest,
    TelegramDeliveryRequest,
    User,
)


@dataclass(frozen=True, slots=True)
class MediaAdmission:
    track: TelegramDeliveryRequest | None = None
    album: TelegramAlbumRequest | None = None
    batch: BatchDownloadRequest | None = None


class TelegramMediaRequestService:
    def __init__(
        self,
        provider: MusicProvider,
        tracks: TelegramTrackRequestService,
        albums: TelegramAlbumRequestService,
        batches: BatchDownloadService | None = None,
        preferences: UserDownloadPreferencesService | None = None,
    ) -> None:
        self._provider = provider
        self._tracks = tracks
        self._albums = albums
        self._batches = batches
        self._preferences = preferences

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
                UnsupportedAlbum,
            ) as exc:
                raise AlbumResolutionFailed() from exc
        if isinstance(reference, PlaylistReference):
            if self._batches is None or self._preferences is None:
                raise UnsupportedMediaType()
            preferences = await self._preferences.get_for_user(user.id)
            confirmation_id = (
                "pl:"
                + hashlib.blake2s(
                    f"{user.id}:{telegram_chat_id}:{source_message_id}".encode(), digest_size=20
                ).hexdigest()
            )
            try:
                batch = await self._batches.expand(
                    user_id=user.id,
                    confirmation_id=confirmation_id,
                    source_type=BatchSourceType.PLAYLIST,
                    source_reference=reference.source_url,
                    preferences=preferences,
                )
            except (
                MetadataUnavailable,
                ProviderAuthenticationError,
                ProviderUnavailable,
                UnsupportedAlbum,
            ) as exc:
                raise AlbumResolutionFailed() from exc
            return MediaAdmission(batch=batch)
        raise TypeError("unsupported media reference")
