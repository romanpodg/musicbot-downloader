"""Provider-safe facade over the isolated OnTheSpot worker process."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.core.enums import (
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    ProviderHealthErrorCode,
    ProviderHealthStatus,
    ProviderRuntimeStatus,
)
from app.core.exceptions import (
    AlbumTooLarge,
    InvalidTrackUrl,
    MetadataUnavailable,
    ProviderUnavailable,
    UnsupportedAlbum,
    UnsupportedMediaType,
    UnsupportedProvider,
)
from app.core.models import (
    AlbumSnapshot,
    AlbumTrackSnapshot,
    NativeMediaInfo,
    NormalizedTrackMetadata,
    PreparedSourceMedia,
    ProviderCapabilities,
    ProviderHealthEntry,
    ProviderSourceCheck,
    TrackSearchCandidate,
    TrackSearchRequest,
)
from app.core.provider_accounts import (
    ProviderAccountComponent,
    ProviderAccountComponentStatus,
    ProviderAccountErrorCode,
    ProviderAccountState,
    ProviderOperationalState,
    SensitiveValue,
)
from app.providers.base import (
    AlbumReference,
    MediaReference,
    MusicProvider,
    ProviderAvailability,
    TrackReference,
)
from app.providers.deezer_authorization import DeezerArlAuthorizationResult
from app.providers.onthespot.capabilities import ONTHESPOT_CAPABILITIES
from app.providers.onthespot.process import OnTheSpotProcessClient, get_shared_process_client
from app.providers.spotify_authorization import (
    SpotifyPlaybackPairingPoll,
    SpotifyPlaybackPairingStart,
    SpotifyWebApiAuthorizationResult,
)
from app.providers.tidal_authorization import (
    TidalDeviceAuthorizationPoll,
    TidalDeviceAuthorizationStart,
)

_SPOTIFY_ID = re.compile(r"^[A-Za-z0-9]{22}$")
_SIMPLE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9._~-]+$")
_SAFE_METADATA_KEYS = frozenset({"item_id", "is_playable", "release_year"})
MAX_ALBUM_TRACKS = 500
_MAX_ALBUM_TEXT_LENGTH = 1024


class OnTheSpotProvider(MusicProvider):
    def __init__(self, process_client: OnTheSpotProcessClient | None = None) -> None:
        self._process_client = process_client or get_shared_process_client()

    async def availability(self) -> ProviderAvailability:
        return await self._process_client.availability()

    async def close(self) -> None:
        await self._process_client.close()

    def detect_url(self, url: str) -> TrackReference:
        reference = self.detect_media(url)
        if not isinstance(reference, TrackReference):
            raise UnsupportedMediaType()
        return reference

    def detect_media(self, url: str) -> MediaReference:
        host, segments, query = _validated_url(url)
        reference = self._detect_known_track(host, segments, query)
        if reference is not None:
            return reference
        album = self._detect_known_album(host, segments)
        if album is not None:
            return album
        if self._is_known_host(host):
            raise UnsupportedMediaType()
        raise UnsupportedProvider()

    async def classify_url(self, url: str) -> MediaReference:
        host, _, _ = _validated_url(url)
        if host not in {"soundcloud.com", "m.soundcloud.com"}:
            return self.detect_media(url)
        raw = await self._process_client.match_url(url)
        service = raw.get("service")
        item_type = raw.get("item_type")
        item_id = raw.get("item_id")
        if service != MusicProviderName.SOUNDCLOUD.value or not isinstance(item_id, str):
            raise UnsupportedMediaType()
        canonical = urlsplit(url)._replace(query="", fragment="").geturl()
        if item_type == "track":
            return TrackReference(MusicProviderName.SOUNDCLOUD, item_id, canonical)
        if item_type == "album":
            return AlbumReference(MusicProviderName.SOUNDCLOUD, item_id, canonical)
        raise UnsupportedMediaType()

    async def get_album(self, url: str) -> AlbumSnapshot:
        reference = await self.classify_url(url)
        if not isinstance(reference, AlbumReference):
            raise UnsupportedAlbum()
        raw = await self._process_client.resolve_album(reference.source_url)
        return _album_snapshot(raw, reference)

    async def get_album_by_id(
        self, provider: MusicProviderName, provider_album_id: str
    ) -> AlbumSnapshot:
        if not provider_album_id or len(provider_album_id) > 2048:
            raise MetadataUnavailable()
        source_url = _provider_album_url(provider, provider_album_id) or ""
        reference = AlbumReference(provider, provider_album_id, source_url)
        raw = await self._process_client.resolve_album_id(provider.value, provider_album_id)
        return _album_snapshot(raw, reference)

    async def get_track_metadata(
        self, provider: MusicProviderName, provider_track_id: str
    ) -> NormalizedTrackMetadata:
        if not provider_track_id or len(provider_track_id) > 2048:
            raise MetadataUnavailable()
        result = await self._process_client.get_track_metadata(provider.value, provider_track_id)
        return _normalized_track_metadata(
            result,
            expected_provider=provider,
            expected_track_id=provider_track_id,
            source_url=_provider_track_url(provider, provider_track_id),
        )

    async def get_metadata(self, url: str) -> NormalizedTrackMetadata:
        detected = self.detect_url(url)
        result = await self._process_client.get_metadata(detected.source_url)
        return _normalized_track_metadata(
            result,
            expected_provider=detected.provider,
            expected_track_id=detected.provider_track_id,
            source_url=detected.source_url,
        )

    async def list_searchable_providers(self) -> tuple[MusicProviderName, ...]:
        raw = await self._process_client.list_searchable_providers()
        providers: list[MusicProviderName] = []
        for value in raw:
            try:
                provider = MusicProviderName(value)
            except ValueError:
                continue
            if provider not in providers:
                providers.append(provider)
        return tuple(providers)

    async def search_tracks(self, request: TrackSearchRequest) -> list[TrackSearchCandidate]:
        raw = await self._process_client.search_tracks(
            request.target_provider.value, request.query, request.limit
        )
        candidates: list[TrackSearchCandidate] = []
        for item in raw:
            url = item.get("url")
            if not isinstance(url, str):
                continue
            try:
                reference = self.detect_url(url)
            except (InvalidTrackUrl, UnsupportedProvider):
                continue
            if reference.provider is not request.target_provider:
                continue
            candidates.append(
                TrackSearchCandidate(
                    provider=reference.provider,
                    provider_track_id=reference.provider_track_id,
                    url=reference.source_url,
                    title=_optional_text(item.get("title")),
                    artist=_optional_text(item.get("artist")),
                )
            )
        return candidates[: request.limit]

    def provider_capabilities(self, provider: MusicProviderName) -> ProviderCapabilities:
        return ONTHESPOT_CAPABILITIES[provider]

    async def refresh_provider_health_state(self) -> None:
        """Reload configured accounts and normal runtime sessions inside the child."""

        await self._process_client.refresh_provider_health()

    async def authorize_deezer_arl(
        self, credential: SensitiveValue
    ) -> DeezerArlAuthorizationResult:
        return await self._process_client.authorize_deezer_arl(credential)

    async def get_spotify_account_components(
        self,
    ) -> tuple[ProviderAccountComponentStatus, ...]:
        raw = await self._process_client.spotify_component_status()
        if set(raw) != {"playback", "web_api"}:
            raise ProviderUnavailable()
        components: list[ProviderAccountComponentStatus] = []
        for name, component in (
            (ProviderAccountComponent.PLAYBACK, raw["playback"]),
            (ProviderAccountComponent.WEB_API, raw["web_api"]),
        ):
            if not isinstance(component, Mapping) or not set(component).issubset(
                {"state", "error_code", "operational_state"}
            ):
                raise ProviderUnavailable()
            raw_state = component.get("state")
            raw_error = component.get("error_code")
            raw_operational = component.get("operational_state")
            if (
                not isinstance(raw_state, str)
                or (raw_error is not None and not isinstance(raw_error, str))
                or (raw_operational is not None and not isinstance(raw_operational, str))
            ):
                raise ProviderUnavailable()
            try:
                state = ProviderAccountState(raw_state)
                error = ProviderAccountErrorCode(raw_error) if raw_error is not None else None
                operational = (
                    ProviderOperationalState(raw_operational)
                    if raw_operational is not None
                    else None
                )
            except ValueError as exc:
                raise ProviderUnavailable() from exc
            components.append(ProviderAccountComponentStatus(name, state, error, operational))
        return tuple(components)

    async def start_spotify_playback_pairing(self) -> SpotifyPlaybackPairingStart:
        return await self._process_client.start_spotify_playback_pairing()

    async def poll_spotify_playback_pairing(self, flow_id: str) -> SpotifyPlaybackPairingPoll:
        return await self._process_client.poll_spotify_playback_pairing(flow_id)

    async def cancel_spotify_playback_pairing(self, flow_id: str) -> None:
        await self._process_client.cancel_spotify_playback_pairing(flow_id)

    async def authorize_spotify_webapi_credentials(
        self, client_id: SensitiveValue, client_secret: SensitiveValue
    ) -> SpotifyWebApiAuthorizationResult:
        return await self._process_client.authorize_spotify_webapi_credentials(
            client_id, client_secret
        )

    async def start_tidal_device_authorization(self) -> TidalDeviceAuthorizationStart:
        return await self._process_client.start_tidal_device_authorization()

    async def poll_tidal_device_authorization(self, flow_id: str) -> TidalDeviceAuthorizationPoll:
        return await self._process_client.poll_tidal_device_authorization(flow_id)

    async def cancel_tidal_device_authorization(self, flow_id: str) -> None:
        await self._process_client.cancel_tidal_device_authorization(flow_id)

    async def check_provider_health(self, provider: MusicProviderName) -> ProviderHealthEntry:
        """Read a sanitized provider-level observation from the isolated worker."""

        raw = await self._process_client.check_provider_health(provider.value)
        raw_status = raw.get("status")
        raw_code = raw.get("error_code")
        raw_requires_auth = raw.get("requires_authentication")
        raw_download_supported = raw.get("download_supported")
        if (
            not isinstance(raw_status, str)
            or not isinstance(raw_requires_auth, bool)
            or not isinstance(raw_download_supported, bool)
            or (raw_code is not None and not isinstance(raw_code, str))
        ):
            raise ProviderUnavailable()
        try:
            status = ProviderHealthStatus(raw_status)
            error_code = ProviderHealthErrorCode(raw_code) if raw_code is not None else None
        except ValueError as exc:
            raise ProviderUnavailable() from exc
        capabilities = self.provider_capabilities(provider)
        if (
            raw_requires_auth is not bool(capabilities.requires_auth)
            or raw_download_supported is not capabilities.download_supported
        ):
            raise ProviderUnavailable()
        return ProviderHealthEntry(
            provider=provider,
            status=status,
            requires_authentication=raw_requires_auth,
            download_supported=raw_download_supported,
            error_code=error_code,
        )

    async def check_source(
        self, provider: MusicProviderName, provider_track_id: str
    ) -> ProviderSourceCheck:
        if not provider_track_id or len(provider_track_id) > 2048:
            raise MetadataUnavailable()
        raw = await self._process_client.check_source(provider.value, provider_track_id)
        raw_status = raw.get("status")
        if not isinstance(raw_status, str):
            raise ProviderUnavailable()
        try:
            status = ProviderRuntimeStatus(raw_status)
        except ValueError as exc:
            raise ProviderUnavailable() from exc

        native = raw.get("native")
        native_info: NativeMediaInfo | None = None
        if native is not None:
            if not isinstance(native, Mapping):
                raise ProviderUnavailable()
            native_info = NativeMediaInfo(
                codec=_wire_enum(NativeCodec, native.get("codec")),
                container=_wire_enum(NativeContainer, native.get("container")),
                bitrate_kbps=_positive_int(native.get("bitrate_kbps")),
            )
        error_code = raw.get("error_code")
        if error_code is not None and not isinstance(error_code, str):
            raise ProviderUnavailable()
        return ProviderSourceCheck(status, native_info, error_code)

    async def prepare_source(
        self, provider: MusicProviderName, provider_track_id: str
    ) -> PreparedSourceMedia | None:
        raw = await self._process_client.prepare_source(provider.value, provider_track_id)
        _raise_runtime_result(raw)
        native = raw.get("native")
        if native is None:
            return None
        if not isinstance(native, Mapping):
            raise ProviderUnavailable()
        return PreparedSourceMedia(
            provider=provider,
            provider_track_id=provider_track_id,
            codec=_wire_enum(NativeCodec, native.get("codec")),
            container=_wire_enum(NativeContainer, native.get("container")),
            bitrate_kbps=_positive_int(native.get("bitrate_kbps")),
            lossless=_optional_bool(native.get("lossless")),
            native_encoded=True,
            provider_decrypted=bool(native.get("provider_decrypted", False)),
            upstream_quality_transcoded=False,
        )

    async def download_source(
        self,
        provider: MusicProviderName,
        provider_track_id: str,
        job_id: str,
        plan_rank: int,
        *,
        timeout_seconds: float,
    ) -> PreparedSourceMedia:
        raw = await self._process_client.download_native(
            provider.value,
            provider_track_id,
            job_id,
            plan_rank,
            timeout_seconds=timeout_seconds,
        )
        _raise_runtime_result(raw)
        path_value = raw.get("file_path")
        if not isinstance(path_value, str):
            raise ProviderUnavailable()
        path = _validated_download_path(
            Path(path_value), self._process_client.temp_dir, job_id, plan_rank
        )
        return PreparedSourceMedia(
            provider=provider,
            provider_track_id=provider_track_id,
            codec=_wire_enum(NativeCodec, raw.get("codec")),
            container=_wire_enum(NativeContainer, raw.get("container")),
            bitrate_kbps=_positive_int(raw.get("bitrate_kbps")),
            lossless=_optional_bool(raw.get("lossless")),
            file_path=path,
            native_encoded=raw.get("native_encoded") is True,
            provider_decrypted=raw.get("provider_decrypted") is True,
            upstream_quality_transcoded=raw.get("upstream_quality_transcoded") is True,
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
    def _detect_known_album(host: str, segments: list[str]) -> AlbumReference | None:
        if host == "open.spotify.com":
            if len(segments) == 3 and segments[0].startswith("intl-"):
                segments = segments[1:]
            if len(segments) == 2 and segments[0] == "album" and _SPOTIFY_ID.fullmatch(segments[1]):
                item_id = segments[1]
                return AlbumReference(
                    MusicProviderName.SPOTIFY,
                    item_id,
                    f"https://open.spotify.com/album/{item_id}",
                )
        if host in {"deezer.com", "www.deezer.com"}:
            if len(segments) == 3 and len(segments[0]) == 2:
                segments = segments[1:]
            if len(segments) == 2 and segments[0] == "album" and segments[1].isdigit():
                item_id = segments[1]
                return AlbumReference(
                    MusicProviderName.DEEZER,
                    item_id,
                    f"https://www.deezer.com/album/{item_id}",
                )
        if host == "music.apple.com" and len(segments) == 4 and segments[1] == "album":
            if len(segments[0]) == 2 and all(_PATH_SEGMENT.fullmatch(item) for item in segments):
                item_id = segments[3]
                return AlbumReference(
                    MusicProviderName.APPLE_MUSIC,
                    item_id,
                    f"https://music.apple.com/{segments[0].lower()}/album/{segments[2]}/{item_id}",
                )
        if host.endswith(".bandcamp.com") and len(segments) == 2 and segments[0] == "album":
            if _PATH_SEGMENT.fullmatch(segments[1]):
                canonical = f"https://{host}/album/{segments[1]}"
                return AlbumReference(MusicProviderName.BANDCAMP, canonical, canonical)
        if host in {"qobuz.com", "www.qobuz.com", "play.qobuz.com", "open.qobuz.com"}:
            if "album" in segments and _SIMPLE_ID.fullmatch(segments[-1]):
                item_id = segments[-1]
                return AlbumReference(
                    MusicProviderName.QOBUZ,
                    item_id,
                    f"https://play.qobuz.com/album/{item_id}",
                )
        if host in {"tidal.com", "www.tidal.com", "listen.tidal.com"} and "album" in segments:
            index = segments.index("album")
            if index + 1 < len(segments) and _SIMPLE_ID.fullmatch(segments[index + 1]):
                item_id = segments[index + 1]
                return AlbumReference(
                    MusicProviderName.TIDAL,
                    item_id,
                    f"https://tidal.com/browse/album/{item_id}",
                )
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


def _validated_url(url: str) -> tuple[str, list[str], str]:
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
    return (
        parsed.hostname.lower(),
        [segment for segment in parsed.path.split("/") if segment],
        parsed.query,
    )


def _normalized_track_metadata(
    result: Mapping[str, Any],
    *,
    expected_provider: MusicProviderName,
    expected_track_id: str,
    source_url: str | None,
) -> NormalizedTrackMetadata:
    service = result.get("service")
    item_type = result.get("item_type")
    item_id = result.get("item_id")
    raw = result.get("metadata")
    if (
        service != expected_provider.value
        or item_type != "track"
        or not isinstance(item_id, str)
        or not isinstance(raw, Mapping)
    ):
        raise MetadataUnavailable()
    resolved_id = str(raw.get("item_id") or item_id)
    if resolved_id != expected_track_id and item_id != expected_track_id:
        raise MetadataUnavailable()
    resolved_url = source_url or _safe_metadata_url(raw.get("item_url"), expected_provider)
    return NormalizedTrackMetadata(
        provider=expected_provider,
        provider_track_id=resolved_id,
        source_url=resolved_url,
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


def _album_snapshot(raw: Mapping[str, Any], reference: AlbumReference) -> AlbumSnapshot:
    if (
        raw.get("provider") != reference.provider.value
        or raw.get("provider_album_id") != reference.provider_album_id
    ):
        raise MetadataUnavailable()
    title = _bounded_album_text(raw.get("title"), required=True)
    artist = _bounded_album_text(raw.get("artist"), required=True)
    raw_tracks = raw.get("tracks")
    if not isinstance(raw_tracks, list) or not raw_tracks:
        raise MetadataUnavailable()
    if len(raw_tracks) > MAX_ALBUM_TRACKS:
        raise AlbumTooLarge()
    tracks: list[AlbumTrackSnapshot] = []
    for expected_position, item in enumerate(raw_tracks, start=1):
        if not isinstance(item, Mapping):
            raise MetadataUnavailable()
        provider_track_id = item.get("provider_track_id")
        position = item.get("position")
        if (
            not isinstance(provider_track_id, str)
            or not provider_track_id
            or len(provider_track_id) > 2048
            or position != expected_position
        ):
            raise MetadataUnavailable()
        tracks.append(
            AlbumTrackSnapshot(
                provider_track_id=provider_track_id,
                position=position,
                title=_bounded_album_text(item.get("title")),
                artist=_bounded_album_text(item.get("artist")),
                disc_number=_wire_positive_int(item.get("disc_number")),
                track_number=_wire_positive_int(item.get("track_number")),
                duration_ms=_wire_positive_int(item.get("duration_ms")),
                explicit=_optional_bool(item.get("explicit")),
            )
        )
    return AlbumSnapshot(
        provider=reference.provider,
        provider_album_id=reference.provider_album_id,
        source_url=reference.source_url,
        title=title or "",
        artist=artist or "",
        tracks=tuple(tracks),
        release_date=_bounded_album_text(raw.get("release_date")),
        duration_ms=_wire_positive_int(raw.get("duration_ms")),
    )


def _bounded_album_text(value: Any, *, required: bool = False) -> str | None:
    text = _optional_text(value)
    if text is None:
        if required:
            raise MetadataUnavailable()
        return None
    if len(text) > _MAX_ALBUM_TEXT_LENGTH:
        raise MetadataUnavailable()
    return text


def _wire_positive_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return int(value)


def _safe_metadata_url(value: Any, provider: MusicProviderName) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        reference = OnTheSpotProvider.__new__(OnTheSpotProvider).detect_url(value)
    except (InvalidTrackUrl, UnsupportedProvider, UnsupportedMediaType):
        return None
    return reference.source_url if reference.provider is provider else None


def _provider_track_url(provider: MusicProviderName, item_id: str) -> str | None:
    if provider is MusicProviderName.APPLE_MUSIC:
        return None
    if provider is MusicProviderName.BANDCAMP:
        return item_id if item_id.startswith(("https://", "http://")) else None
    if provider is MusicProviderName.DEEZER:
        return f"https://www.deezer.com/track/{item_id}"
    if provider is MusicProviderName.QOBUZ:
        return f"https://play.qobuz.com/track/{item_id}"
    if provider is MusicProviderName.SPOTIFY:
        return f"https://open.spotify.com/track/{item_id}"
    if provider is MusicProviderName.TIDAL:
        return f"https://tidal.com/browse/track/{item_id}"
    if provider is MusicProviderName.YOUTUBE_MUSIC:
        return f"https://music.youtube.com/watch?v={item_id}"
    return None


def _provider_album_url(provider: MusicProviderName, item_id: str) -> str | None:
    if provider is MusicProviderName.APPLE_MUSIC:
        return f"https://music.apple.com/us/album/release/{item_id}"
    if provider is MusicProviderName.BANDCAMP:
        return item_id if item_id.startswith(("https://", "http://")) else None
    if provider is MusicProviderName.DEEZER:
        return f"https://www.deezer.com/album/{item_id}"
    if provider is MusicProviderName.QOBUZ:
        return f"https://play.qobuz.com/album/{item_id}"
    if provider is MusicProviderName.SPOTIFY:
        return f"https://open.spotify.com/album/{item_id}"
    if provider is MusicProviderName.TIDAL:
        return f"https://tidal.com/browse/album/{item_id}"
    if provider is MusicProviderName.SOUNDCLOUD and item_id.startswith(("https://", "http://")):
        return item_id
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
    return duration if duration > 0 else None


def _positive_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ProviderUnavailable() from None
    if number <= 0:
        raise ProviderUnavailable()
    return number


def _wire_enum(enum_type: type[NativeCodec] | type[NativeContainer], value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProviderUnavailable()
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ProviderUnavailable() from exc


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _raise_runtime_result(raw: Mapping[str, Any]) -> None:
    status = raw.get("status")
    if status in {None, ProviderRuntimeStatus.AVAILABLE.value}:
        return
    if status == ProviderRuntimeStatus.AUTH_REQUIRED.value:
        from app.core.exceptions import ProviderAuthenticationError

        raise ProviderAuthenticationError()
    if status == ProviderRuntimeStatus.SOURCE_UNAVAILABLE.value:
        raise MetadataUnavailable()
    raise ProviderUnavailable()


def _validated_download_path(path: Path, root: Path, job_id: str, plan_rank: int) -> Path:
    resolved = path.resolve()
    expected = (root / job_id / f"attempt-{plan_rank:03d}" / "source").resolve()
    if (
        expected not in resolved.parents
        or resolved.name.startswith(".")
        or resolved.suffix in {".part", ".partial", ".tmp"}
    ):
        raise ProviderUnavailable()
    return resolved


def _release_date(raw: Mapping[str, Any]) -> date | None:
    value = raw.get("release_date")
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
