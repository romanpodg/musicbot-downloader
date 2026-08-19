"""Durable opaque Telegram deep-link registration and public resolution."""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
from dataclasses import dataclass

from app.core.enums import DeepLinkTargetType, MusicProviderName
from app.core.exceptions import (
    DatabaseConcurrencyError,
    DeepLinkNotFound,
    IdempotencyKeyConflict,
)
from app.providers.base import AlbumReference, MusicProvider, TrackReference
from app.services.track_resolution import ResolveTrackService
from app.storage import Database
from app.storage.models import DeepLinkRegistryEntry
from app.storage.models.base import utc_now

START_PARAMETER_PREFIX = "d1_"
START_PARAMETER_LENGTH = 35
MAX_IDEMPOTENCY_KEY_LENGTH = 128
MAX_REGISTRATION_URL_LENGTH = 2048
MAX_TOKEN_ATTEMPTS = 5
_START_PARAMETER = re.compile(r"^d1_[A-Za-z0-9_-]{32}$", re.ASCII)


@dataclass(frozen=True, slots=True)
class DeepLinkRegistration:
    entry: DeepLinkRegistryEntry
    created: bool


class DeepLinkRegistryService:
    """Application boundary shared by the private API and Telegram transport."""

    def __init__(
        self,
        database: Database,
        provider: MusicProvider,
        track_resolver: ResolveTrackService,
        *,
        telegram_bot_id: int,
    ) -> None:
        if telegram_bot_id <= 0:
            raise ValueError("telegram_bot_id must be positive")
        self._database = database
        self._provider = provider
        self._track_resolver = track_resolver
        self._telegram_bot_id = telegram_bot_id

    async def register_from_url(
        self, url: str, *, idempotency_key: str | None = None
    ) -> DeepLinkRegistration:
        if not url or len(url) > MAX_REGISTRATION_URL_LENGTH:
            from app.core.exceptions import InvalidTrackUrl

            raise InvalidTrackUrl()
        key = self._validate_idempotency_key(idempotency_key)
        reference = await self._provider.classify_url(url)
        target_type = (
            DeepLinkTargetType.TRACK
            if isinstance(reference, TrackReference)
            else DeepLinkTargetType.ALBUM
        )
        fingerprint = self._fingerprint(reference, target_type)
        replay = await self._idempotency_replay(key, fingerprint)
        if replay is not None:
            return DeepLinkRegistration(replay, False)

        track_id: int | None = None
        album_provider: MusicProviderName | None = None
        album_provider_id: str | None = None
        if isinstance(reference, TrackReference):
            resolved = await self._track_resolver.resolve(reference.source_url, discover=True)
            track_id = resolved.track.id
        elif isinstance(reference, AlbumReference):
            album_provider = reference.provider
            album_provider_id = reference.provider_album_id
            if not album_provider_id or len(album_provider_id) > 2048:
                from app.core.exceptions import UnsupportedMediaType

                raise UnsupportedMediaType()
        else:
            from app.core.exceptions import UnsupportedMediaType

            raise UnsupportedMediaType()

        for attempt in range(MAX_TOKEN_ATTEMPTS):
            token = self.generate_start_parameter()
            try:
                async with self._database.transaction() as repositories:
                    if key is not None:
                        existing = await repositories.deep_links.get_by_idempotency_key(
                            self._telegram_bot_id, key
                        )
                        if existing is not None:
                            self._require_same_fingerprint(existing, fingerprint)
                            return DeepLinkRegistration(existing, False)
                    entry = await repositories.deep_links.create(
                        telegram_bot_id=self._telegram_bot_id,
                        token=token,
                        target_type=target_type,
                        request_fingerprint=fingerprint,
                        idempotency_key=key,
                        track_id=track_id,
                        album_provider=album_provider,
                        album_provider_id=album_provider_id,
                    )
                return DeepLinkRegistration(entry, True)
            except DatabaseConcurrencyError:
                replay = await self._idempotency_replay(key, fingerprint)
                if replay is not None:
                    return DeepLinkRegistration(replay, False)
                if attempt == MAX_TOKEN_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(0)
        raise DatabaseConcurrencyError()

    async def get_by_token(self, token: str) -> DeepLinkRegistryEntry:
        if not self.parse_start_parameter(token):
            raise DeepLinkNotFound()
        async with self._database.transaction() as repositories:
            entry = await repositories.deep_links.get_by_token(self._telegram_bot_id, token)
            if entry is None:
                raise DeepLinkNotFound()
            return entry

    async def resolve_start_payload(self, payload: str) -> DeepLinkRegistryEntry | None:
        if not self.parse_start_parameter(payload):
            return None
        async with self._database.transaction() as repositories:
            return await repositories.deep_links.get_active_by_token(self._telegram_bot_id, payload)

    async def revoke(self, token: str) -> DeepLinkRegistryEntry:
        if not self.parse_start_parameter(token):
            raise DeepLinkNotFound()
        for attempt in range(MAX_TOKEN_ATTEMPTS):
            try:
                async with self._database.transaction() as repositories:
                    entry = await repositories.deep_links.revoke_by_token(
                        self._telegram_bot_id, token, now=utc_now()
                    )
                    if entry is None:
                        raise DeepLinkNotFound()
                    return entry
            except DatabaseConcurrencyError:
                if attempt == MAX_TOKEN_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(0.02 * (attempt + 1))
        raise DatabaseConcurrencyError()

    async def _idempotency_replay(
        self, key: str | None, fingerprint: str
    ) -> DeepLinkRegistryEntry | None:
        if key is None:
            return None
        async with self._database.transaction() as repositories:
            entry = await repositories.deep_links.get_by_idempotency_key(self._telegram_bot_id, key)
            if entry is None:
                return None
            self._require_same_fingerprint(entry, fingerprint)
            return entry

    @staticmethod
    def _require_same_fingerprint(entry: DeepLinkRegistryEntry, fingerprint: str) -> None:
        if not secrets.compare_digest(entry.request_fingerprint, fingerprint):
            raise IdempotencyKeyConflict()

    @staticmethod
    def _validate_idempotency_key(value: str | None) -> str | None:
        if value is None:
            return None
        if not value or value != value.strip() or len(value) > MAX_IDEMPOTENCY_KEY_LENGTH:
            raise ValueError("invalid idempotency key")
        return value

    @staticmethod
    def _fingerprint(
        reference: TrackReference | AlbumReference, target_type: DeepLinkTargetType
    ) -> str:
        provider_id = (
            reference.provider_track_id
            if isinstance(reference, TrackReference)
            else reference.provider_album_id
        )
        normalized = f"{target_type.value}\0{reference.provider.value}\0{provider_id}"
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def generate_start_parameter() -> str:
        return START_PARAMETER_PREFIX + secrets.token_urlsafe(24)

    @staticmethod
    def parse_start_parameter(value: str) -> bool:
        return (
            len(value) == START_PARAMETER_LENGTH and _START_PARAMETER.fullmatch(value) is not None
        )

    @staticmethod
    def is_namespaced_payload(value: str) -> bool:
        return value.startswith(START_PARAMETER_PREFIX)


def deep_link_url(username: str, start_parameter: str) -> str:
    normalized = username.removeprefix("@").strip()
    if not normalized or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", normalized, re.ASCII):
        raise ValueError("Telegram bot username is unavailable")
    if not DeepLinkRegistryService.parse_start_parameter(start_parameter):
        raise ValueError("invalid start parameter")
    return f"https://t.me/{normalized}?start={start_parameter}"
