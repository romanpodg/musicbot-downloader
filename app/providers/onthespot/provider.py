"""Async adapter over OnTheSpot's synchronous, internal Python functions."""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import importlib.util
import os
import re
import threading
from collections.abc import Mapping
from datetime import date
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from app.core.enums import MusicProviderName
from app.core.exceptions import (
    InvalidTrackUrl,
    MetadataUnavailable,
    ProviderAuthenticationError,
    ProviderUnavailable,
    UnsupportedProvider,
)
from app.core.models import NativeMediaInfo, NormalizedTrackMetadata
from app.providers.base import MusicProvider, ProviderAvailability, TrackReference

_SPOTIFY_ID = re.compile(r"^[A-Za-z0-9]{22}$")
_SIMPLE_ID = re.compile(r"^[A-Za-z0-9-]+$")
_AUTHENTICATED_SERVICES = frozenset(
    {"apple_music", "deezer", "qobuz", "soundcloud", "spotify", "tidal"}
)
_SAFE_METADATA_KEYS = frozenset({"item_id", "is_playable", "release_year"})
_SENSITIVE_QUERY_PARTS = ("token", "secret", "cookie", "credential", "auth", "key")


class OnTheSpotBridge(Protocol):
    def availability(self) -> ProviderAvailability: ...

    def resolve(self, url: str) -> tuple[str, str, str, Mapping[str, Any]]: ...


class PythonOnTheSpotBridge:
    """The only class allowed to import OnTheSpot internals."""

    def __init__(self) -> None:
        self._initialized = False
        self._lock = threading.Lock()

    def availability(self) -> ProviderAvailability:
        if importlib.util.find_spec("onthespot") is None:
            return ProviderAvailability(False, detail="OnTheSpot package is not installed")
        try:
            version = importlib.metadata.version("onthespot")
            required_modules = ("onthespot.api.registry", "onthespot.parse_item")
            if any(importlib.util.find_spec(module) is None for module in required_modules):
                return ProviderAvailability(False, detail="Required OnTheSpot modules are missing")
        except Exception as exc:
            return ProviderAvailability(False, detail=type(exc).__name__)
        return ProviderAvailability(True, version=version)

    def resolve(self, url: str) -> tuple[str, str, str, Mapping[str, Any]]:
        with self._lock:
            try:
                self._initialize()
            except Exception as exc:
                raise ProviderUnavailable() from exc

            try:
                parse_module = importlib.import_module("onthespot.parse_item")
                accounts_module = importlib.import_module("onthespot.accounts")
                registry_module = importlib.import_module("onthespot.api.registry")
                resolved = parse_module.UrlMatcher().match(url)
            except (ImportError, ModuleNotFoundError) as exc:
                raise ProviderUnavailable() from exc
            except Exception as exc:
                raise MetadataUnavailable() from exc

            if resolved is None:
                raise InvalidTrackUrl()
            service, item_type, item_id = resolved
            if service == "__handled__" or not item_id:
                raise InvalidTrackUrl()
            if item_type != "track":
                raise InvalidTrackUrl()

            try:
                token = accounts_module.get_account_token(service)
                if service in _AUTHENTICATED_SERVICES and token is None:
                    raise ProviderAuthenticationError()
                metadata_function = registry_module.get_metadata_function(service, item_type)
                metadata = metadata_function(token, item_id)
            except ProviderAuthenticationError:
                raise
            except (KeyError, IndexError) as exc:
                raise ProviderAuthenticationError() from exc
            except Exception as exc:
                raise MetadataUnavailable() from exc

            if not isinstance(metadata, Mapping) or not metadata:
                raise MetadataUnavailable()
            return str(service), str(item_type), str(item_id), metadata

    def _initialize(self) -> None:
        if self._initialized:
            return
        # The pinned librespot package contains legacy generated Protobuf
        # descriptors, while OnTheSpot's pywidevine dependency requires a
        # modern Protobuf runtime. Pure-Python parsing is the compatible mode
        # recommended by Protobuf for this otherwise unsatisfiable combination.
        os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
        accounts_module = importlib.import_module("onthespot.accounts")
        loader = accounts_module.AccountPoolLoader(gui=False)
        loader.run()
        self._initialized = True


class OnTheSpotProvider(MusicProvider):
    def __init__(self, bridge: OnTheSpotBridge | None = None) -> None:
        self._bridge = bridge or PythonOnTheSpotBridge()

    async def availability(self) -> ProviderAvailability:
        return await asyncio.to_thread(self._bridge.availability)

    def detect_url(self, url: str) -> TrackReference:
        if not url or len(url) > 2048:
            raise InvalidTrackUrl()
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise InvalidTrackUrl() from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise InvalidTrackUrl()
        query_keys = (key.lower() for key in parse_qs(parsed.query, keep_blank_values=True))
        if any(part in key for key in query_keys for part in _SENSITIVE_QUERY_PARTS):
            raise InvalidTrackUrl()

        host = parsed.hostname.lower()
        segments = [segment for segment in parsed.path.split("/") if segment]
        reference = self._detect_known_track(host, segments, parsed.query, url)
        if reference is None:
            known_hosts = (
                host == "music.apple.com"
                or host == "open.spotify.com"
                or host.endswith(".bandcamp.com")
                or host
                in {
                    "deezer.com",
                    "www.deezer.com",
                    "qobuz.com",
                    "www.qobuz.com",
                    "play.qobuz.com",
                    "open.qobuz.com",
                    "soundcloud.com",
                    "m.soundcloud.com",
                    "tidal.com",
                    "www.tidal.com",
                    "listen.tidal.com",
                    "music.youtube.com",
                }
            )
            if known_hosts:
                raise InvalidTrackUrl()
            raise UnsupportedProvider()
        return reference

    async def get_metadata(self, url: str) -> NormalizedTrackMetadata:
        detected = self.detect_url(url)
        availability = await self.availability()
        if not availability.available:
            raise ProviderUnavailable()

        service, item_type, item_id, raw = await asyncio.to_thread(self._bridge.resolve, url)
        if item_type != "track" or service != detected.provider.value:
            raise InvalidTrackUrl()
        provider = MusicProviderName(service)
        resolved_id = str(raw.get("item_id") or item_id)
        return NormalizedTrackMetadata(
            provider=provider,
            provider_track_id=resolved_id,
            source_url=url,
            title=_optional_text(raw.get("title")),
            artist=_optional_text(raw.get("artists")),
            album=_optional_text(raw.get("album_name")),
            isrc=_optional_text(raw.get("isrc")),
            duration_ms=_duration_ms(raw.get("length")),
            release_date=_release_date(raw),
            explicit=_optional_bool(raw.get("explicit")),
            native=NativeMediaInfo(),
            provider_metadata={key: raw[key] for key in _SAFE_METADATA_KEYS if key in raw},
        )

    @staticmethod
    def _detect_known_track(
        host: str, segments: list[str], query: str, original_url: str
    ) -> TrackReference | None:
        if host == "open.spotify.com":
            if len(segments) == 3 and segments[0].startswith("intl-"):
                segments = segments[1:]
            if len(segments) == 2 and segments[0] == "track" and _SPOTIFY_ID.fullmatch(segments[1]):
                return TrackReference(MusicProviderName.SPOTIFY, segments[1], original_url)

        if host in {"deezer.com", "www.deezer.com"}:
            if len(segments) == 3 and len(segments[0]) == 2:
                segments = segments[1:]
            if len(segments) == 2 and segments[0] == "track" and segments[1].isdigit():
                return TrackReference(MusicProviderName.DEEZER, segments[1], original_url)

        if host == "music.apple.com" and len(segments) >= 4 and segments[1] == "album":
            track_ids = parse_qs(query).get("i", [])
            if track_ids and track_ids[0].isdigit():
                return TrackReference(MusicProviderName.APPLE_MUSIC, track_ids[0], original_url)

        if host.endswith(".bandcamp.com") and len(segments) == 2 and segments[0] == "track":
            return TrackReference(MusicProviderName.BANDCAMP, original_url, original_url)

        if host in {"qobuz.com", "www.qobuz.com", "play.qobuz.com", "open.qobuz.com"}:
            if "track" in segments:
                track_index = segments.index("track")
                item_id = segments[-1]
                if track_index < len(segments) - 1 and _SIMPLE_ID.fullmatch(item_id):
                    return TrackReference(MusicProviderName.QOBUZ, item_id, original_url)

        if host in {"tidal.com", "www.tidal.com", "listen.tidal.com"}:
            if "track" in segments:
                track_index = segments.index("track")
                if track_index + 1 < len(segments) and _SIMPLE_ID.fullmatch(
                    segments[track_index + 1]
                ):
                    item_id = segments[track_index + 1]
                    return TrackReference(MusicProviderName.TIDAL, item_id, original_url)

        if host == "music.youtube.com" and segments == ["watch"]:
            video_ids = parse_qs(query).get("v", [])
            if video_ids and _SIMPLE_ID.fullmatch(video_ids[0]):
                return TrackReference(MusicProviderName.YOUTUBE_MUSIC, video_ids[0], original_url)

        if host in {"soundcloud.com", "m.soundcloud.com"} and len(segments) == 2:
            return TrackReference(MusicProviderName.SOUNDCLOUD, original_url, original_url)

        return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _duration_ms(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        duration = int(value)
    except (TypeError, ValueError):
        return None
    return duration if duration >= 0 else None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _release_date(raw: Mapping[str, Any]) -> date | None:
    value = raw.get("release_date")
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
