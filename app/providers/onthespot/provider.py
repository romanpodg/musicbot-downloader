"""Provider-safe facade over the isolated OnTheSpot worker process."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.core.enums import MusicProviderName
from app.core.exceptions import InvalidTrackUrl, MetadataUnavailable, UnsupportedProvider
from app.core.models import NativeMediaInfo, NormalizedTrackMetadata
from app.providers.base import MusicProvider, ProviderAvailability, TrackReference
from app.providers.onthespot.process import OnTheSpotProcessClient, get_shared_process_client

_SPOTIFY_ID = re.compile(r"^[A-Za-z0-9]{22}$")
_SIMPLE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9._~-]+$")
_SAFE_METADATA_KEYS = frozenset({"item_id", "is_playable", "release_year"})


class OnTheSpotProvider(MusicProvider):
    def __init__(self, process_client: OnTheSpotProcessClient | None = None) -> None:
        self._process_client = process_client or get_shared_process_client()

    async def availability(self) -> ProviderAvailability:
        return await self._process_client.availability()

    async def close(self) -> None:
        await self._process_client.close()

    def detect_url(self, url: str) -> TrackReference:
        if not url or len(url) > 2048:
            raise InvalidTrackUrl()
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise InvalidTrackUrl() from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise InvalidTrackUrl()
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise InvalidTrackUrl()
        expected_port = 80 if parsed.scheme == "http" else 443
        if port is not None and port != expected_port:
            raise InvalidTrackUrl()

        host = parsed.hostname.lower()
        segments = [segment for segment in parsed.path.split("/") if segment]
        reference = self._detect_known_track(host, segments, parsed.query)
        if reference is not None:
            return reference
        if self._is_known_host(host):
            raise InvalidTrackUrl()
        raise UnsupportedProvider()

    async def get_metadata(self, url: str) -> NormalizedTrackMetadata:
        detected = self.detect_url(url)
        result = await self._process_client.get_metadata(detected.source_url)
        service = result.get("service")
        item_type = result.get("item_type")
        item_id = result.get("item_id")
        raw = result.get("metadata")
        if (
            service != detected.provider.value
            or item_type != "track"
            or not isinstance(item_id, str)
            or not isinstance(raw, Mapping)
        ):
            raise MetadataUnavailable()

        provider = MusicProviderName(service)
        resolved_id = str(raw.get("item_id") or item_id)
        return NormalizedTrackMetadata(
            provider=provider,
            provider_track_id=resolved_id,
            source_url=detected.source_url,
            title=_optional_text(raw.get("title")),
            artist=_optional_text(raw.get("artists")),
            album=_optional_text(raw.get("album_name")),
            isrc=_optional_text(raw.get("isrc")),
            duration_ms=_duration_ms(raw.get("length")),
            release_date=_release_date(raw),
            explicit=_optional_bool(raw.get("explicit")),
            native=NativeMediaInfo(),
            provider_metadata={
                key: raw[key] for key in _SAFE_METADATA_KEYS if key in raw and raw[key] is not None
            },
        )

    @staticmethod
    def _detect_known_track(host: str, segments: list[str], query: str) -> TrackReference | None:
        if host == "open.spotify.com":
            if len(segments) == 3 and segments[0].startswith("intl-"):
                segments = segments[1:]
            if len(segments) == 2 and segments[0] == "track" and _SPOTIFY_ID.fullmatch(segments[1]):
                item_id = segments[1]
                return TrackReference(
                    MusicProviderName.SPOTIFY,
                    item_id,
                    f"https://open.spotify.com/track/{item_id}",
                )

        if host in {"deezer.com", "www.deezer.com"}:
            if len(segments) == 3 and len(segments[0]) == 2:
                segments = segments[1:]
            if len(segments) == 2 and segments[0] == "track" and segments[1].isdigit():
                item_id = segments[1]
                return TrackReference(
                    MusicProviderName.DEEZER,
                    item_id,
                    f"https://www.deezer.com/track/{item_id}",
                )

        if host == "music.apple.com" and len(segments) == 4 and segments[1] == "album":
            track_ids = parse_qs(query, keep_blank_values=True).get("i", [])
            if (
                len(segments[0]) == 2
                and all(_PATH_SEGMENT.fullmatch(segment) for segment in segments)
                and len(track_ids) == 1
                and track_ids[0].isdigit()
            ):
                item_id = track_ids[0]
                canonical = (
                    f"https://music.apple.com/{segments[0].lower()}/album/"
                    f"{segments[2]}/{segments[3]}?i={item_id}"
                )
                return TrackReference(MusicProviderName.APPLE_MUSIC, item_id, canonical)

        if host.endswith(".bandcamp.com") and len(segments) == 2 and segments[0] == "track":
            if _PATH_SEGMENT.fullmatch(segments[1]):
                canonical = f"https://{host}/track/{segments[1]}"
                return TrackReference(MusicProviderName.BANDCAMP, canonical, canonical)

        if host in {"qobuz.com", "www.qobuz.com", "play.qobuz.com", "open.qobuz.com"}:
            if "track" in segments:
                track_index = segments.index("track")
                if track_index + 1 < len(segments):
                    item_id = segments[-1]
                    if _SIMPLE_ID.fullmatch(item_id):
                        canonical = f"https://play.qobuz.com/track/{item_id}"
                        return TrackReference(MusicProviderName.QOBUZ, item_id, canonical)

        if host in {"tidal.com", "www.tidal.com", "listen.tidal.com"} and "track" in segments:
            track_index = segments.index("track")
            if track_index + 1 < len(segments) and _SIMPLE_ID.fullmatch(segments[track_index + 1]):
                item_id = segments[track_index + 1]
                canonical = f"https://tidal.com/browse/track/{item_id}"
                return TrackReference(MusicProviderName.TIDAL, item_id, canonical)

        if host == "music.youtube.com" and segments == ["watch"]:
            video_ids = parse_qs(query, keep_blank_values=True).get("v", [])
            if len(video_ids) == 1 and _SIMPLE_ID.fullmatch(video_ids[0]):
                item_id = video_ids[0]
                canonical = f"https://music.youtube.com/watch?v={item_id}"
                return TrackReference(MusicProviderName.YOUTUBE_MUSIC, item_id, canonical)

        if host in {"soundcloud.com", "m.soundcloud.com"} and len(segments) == 2:
            if all(_PATH_SEGMENT.fullmatch(segment) for segment in segments):
                canonical = f"https://soundcloud.com/{segments[0]}/{segments[1]}"
                return TrackReference(MusicProviderName.SOUNDCLOUD, canonical, canonical)

        return None

    @staticmethod
    def _is_known_host(host: str) -> bool:
        return host in {
            "deezer.com",
            "www.deezer.com",
            "music.apple.com",
            "music.youtube.com",
            "open.spotify.com",
            "qobuz.com",
            "www.qobuz.com",
            "play.qobuz.com",
            "open.qobuz.com",
            "soundcloud.com",
            "m.soundcloud.com",
            "tidal.com",
            "www.tidal.com",
            "listen.tidal.com",
        } or host.endswith(".bandcamp.com")


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
