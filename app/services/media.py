"""Safe ffprobe/FFmpeg boundary owned by the application."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from app.core.enums import DownloadFailureCode, MusicProviderName, NativeCodec, NativeContainer
from app.core.models import OutputSpecification, PreparedSourceMedia, SourceMediaRequirement

OUTPUT_DURATION_TOLERANCE_MS: Final = 3000
BITRATE_TOLERANCE_RATIO: Final = 0.20
SOURCE_BITRATE_TOLERANCE_RATIO: Final = 0.05


class MediaOperationError(Exception):
    def __init__(self, code: DownloadFailureCode) -> None:
        super().__init__()
        self.code = code


class ArtworkFetcher:
    """Bounded, scheme-restricted optional artwork acquisition."""

    def __init__(self, *, timeout: float = 10.0, max_bytes: int = 5 * 1024 * 1024) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes

    async def fetch(self, url: str, destination: Path) -> bool:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False

        def _read() -> bytes:
            request = urllib.request.Request(url, headers={"User-Agent": "musicbot/1"})
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                data = response.read(self.max_bytes + 1)
            return bytes(data)

        try:
            data = await asyncio.to_thread(_read)
        except Exception:
            return False
        if len(data) == 0 or len(data) > self.max_bytes or not _supported_image(data):
            return False
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(destination.write_bytes, data)
        return True


def _supported_image(data: bytes) -> bool:
    return data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff")


class MediaProbe:
    def __init__(self, temp_root: Path, binary: str | None = None, *, timeout: float = 30) -> None:
        self._temp_root = temp_root.expanduser().resolve()
        self._binary = binary or "ffprobe"
        self._timeout = timeout

    async def probe(
        self,
        path: Path,
        *,
        provider: MusicProviderName,
        provider_track_id: str,
        native_encoded: bool,
        provider_decrypted: bool = False,
        upstream_quality_transcoded: bool = False,
    ) -> PreparedSourceMedia:
        target = _owned_file(path, self._temp_root)
        _validate_probe_target(target)
        if not self.available():
            raise MediaOperationError(DownloadFailureCode.MEDIA_PROBE_UNAVAILABLE)
        command = (
            self._binary,
            "-v",
            "error",
            "-show_entries",
            "format=format_name,duration,bit_rate:stream=codec_type,codec_name,bit_rate,"
            "sample_rate,bits_per_sample,bits_per_raw_sample,channels,duration",
            "-of",
            "json",
            os.fspath(target),
        )
        stdout = await _run(
            command,
            timeout_seconds=self._timeout,
            failure=DownloadFailureCode.MEDIA_PROBE_FAILED,
        )
        try:
            payload = json.loads(stdout)
            streams = payload["streams"]
            raw_format = payload["format"]
            audio = next(item for item in streams if item.get("codec_type") == "audio")
        except (json.JSONDecodeError, KeyError, StopIteration, TypeError) as exc:
            raise MediaOperationError(DownloadFailureCode.MEDIA_PROBE_FAILED) from exc
        codec = _codec(audio.get("codec_name"))
        container = _container(raw_format.get("format_name"), codec)
        bitrate = _kbps(audio.get("bit_rate")) or _kbps(raw_format.get("bit_rate"))
        duration_ms = _duration_ms(audio.get("duration")) or _duration_ms(
            raw_format.get("duration")
        )
        bit_depth = _positive_int(audio.get("bits_per_raw_sample")) or _positive_int(
            audio.get("bits_per_sample")
        )
        return PreparedSourceMedia(
            provider=provider,
            provider_track_id=provider_track_id,
            codec=codec,
            container=container,
            bitrate_kbps=bitrate,
            sample_rate_hz=_positive_int(audio.get("sample_rate")),
            bit_depth=bit_depth,
            channels=_positive_int(audio.get("channels")),
            duration_ms=duration_ms,
            lossless=(
                codec is NativeCodec.FLAC and native_encoded and not upstream_quality_transcoded
            ),
            file_path=target,
            native_encoded=native_encoded,
            provider_decrypted=provider_decrypted,
            upstream_quality_transcoded=upstream_quality_transcoded,
        )

    def available(self) -> bool:
        return shutil.which(self._binary) is not None or Path(self._binary).is_file()


class Transcoder:
    def __init__(self, temp_root: Path, binary: str | None = None, *, timeout: float = 300) -> None:
        self._temp_root = temp_root.expanduser().resolve()
        self._binary = binary or "ffmpeg"
        self._timeout = timeout

    def available(self) -> bool:
        return shutil.which(self._binary) is not None or Path(self._binary).is_file()

    def command(
        self,
        source: Path,
        partial_output: Path,
        output: OutputSpecification,
        metadata: Mapping[str, str],
        artwork: Path | None = None,
    ) -> tuple[str, ...]:
        source = _owned_file(source, self._temp_root)
        partial_output = _owned_path(partial_output, self._temp_root)
        common = [
            self._binary,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            os.fspath(source),
            "-map",
            "0:a:0",
            "-vn",
        ]
        if artwork is not None:
            artwork = _owned_file(artwork, self._temp_root)
            common.extend(["-i", os.fspath(artwork), "-map", "1:0"])
        if output.codec is NativeCodec.MP3 and output.bitrate_kbps in {128, 320}:
            encoding = [
                "-c:a",
                "libmp3lame",
                "-b:a",
                f"{output.bitrate_kbps}k",
                "-id3v2_version",
                "3",
                "-f",
                "mp3",
            ]
        elif output.codec is NativeCodec.AAC and output.bitrate_kbps == 256:
            encoding = [
                "-c:a",
                "aac",
                "-b:a",
                "256k",
                "-movflags",
                "+faststart",
                "-f",
                "ipod",
            ]
        elif output.codec is NativeCodec.FLAC and output.container is NativeContainer.FLAC:
            encoding = ["-c:a", "flac", "-f", "flac"]
        else:
            raise MediaOperationError(DownloadFailureCode.INVALID_PLAN)
        tags: list[str] = []
        if artwork is not None:
            tags.extend(("-c:v", "mjpeg", "-disposition:v", "attached_pic"))
        for key in (
            "title",
            "artist",
            "album",
            "album_artist",
            "track",
            "disc",
            "date",
            "isrc",
            "copyright",
            "explicit",
        ):
            value = metadata.get(key)
            if value:
                tags.extend(("-metadata", f"{key}={value}"))
        return tuple((*common, *encoding, *tags, os.fspath(partial_output)))

    async def transcode(
        self,
        source: Path,
        partial_output: Path,
        output: OutputSpecification,
        metadata: Mapping[str, str],
        artwork: Path | None = None,
    ) -> None:
        if not self.available():
            raise MediaOperationError(DownloadFailureCode.TRANSCODER_UNAVAILABLE)
        command = self.command(source, partial_output, output, metadata, artwork)
        await _run(
            command,
            timeout_seconds=self._timeout,
            failure=DownloadFailureCode.TRANSCODE_FAILED,
        )

    async def tag_copy(
        self, path: Path, metadata: Mapping[str, str], artwork: Path | None = None
    ) -> bool:
        """Best-effort stream-copy tagging; absence/failure never degrades audio."""

        if not self.available():
            return False
        source = _owned_file(path, self._temp_root)
        muxer = {".mp3": "mp3", ".m4a": "ipod", ".flac": "flac"}.get(source.suffix.lower())
        if muxer is None:
            return False
        partial = source.with_suffix(source.suffix + ".tagging.partial")
        command = [
            self._binary,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            os.fspath(source),
            "-map",
            "0:a:0",
            "-c",
            "copy",
        ]
        if artwork is not None:
            artwork = _owned_file(artwork, self._temp_root)
            command.extend(
                (
                    "-i",
                    os.fspath(artwork),
                    "-map",
                    "0",
                    "-map",
                    "1:0",
                    "-c:v",
                    "mjpeg",
                    "-disposition:v",
                    "attached_pic",
                )
            )
        if not metadata:
            command.extend(("-map_metadata", "-1"))
        for key in (
            "title",
            "artist",
            "album",
            "album_artist",
            "track",
            "disc",
            "date",
            "isrc",
            "copyright",
            "explicit",
        ):
            value = metadata.get(key)
            if value:
                command.extend(("-metadata", f"{key}={value}"))
        command.extend(("-f", muxer, os.fspath(partial)))
        try:
            await _run(
                command,
                timeout_seconds=self._timeout,
                failure=DownloadFailureCode.TRANSCODE_FAILED,
            )
            os.replace(partial, source)
        except MediaOperationError:
            return False
        finally:
            partial.unlink(missing_ok=True)
        return True


def media_satisfies_requirement(
    media: PreparedSourceMedia, requirement: SourceMediaRequirement
) -> bool:
    if media.upstream_quality_transcoded or not media.native_encoded:
        return False
    if requirement.required_lossless is True and media.lossless is not True:
        return False
    if requirement.required_codec is not None and media.codec is not requirement.required_codec:
        return False
    if requirement.required_bitrate_kbps is not None:
        if media.bitrate_kbps is None:
            return False
        target = requirement.required_bitrate_kbps
        if abs(media.bitrate_kbps - target) > max(
            8, round(target * SOURCE_BITRATE_TOLERANCE_RATIO)
        ):
            return False
    return True


def output_satisfies_specification(
    media: PreparedSourceMedia,
    output: OutputSpecification,
    expected_duration_ms: int | None,
) -> bool:
    if (
        media.file_path is None
        or not media.file_path.is_file()
        or media.file_path.stat().st_size <= 0
    ):
        return False
    if expected_duration_ms is not None:
        if (
            media.duration_ms is None
            or abs(media.duration_ms - expected_duration_ms) > OUTPUT_DURATION_TOLERANCE_MS
        ):
            return False
    if output.lossless:
        return media.lossless is True
    if media.codec is not output.codec or media.container is not output.container:
        return False
    if output.bitrate_kbps is None:
        return True
    if media.bitrate_kbps is None:
        return False
    return abs(media.bitrate_kbps - output.bitrate_kbps) <= max(
        16, round(output.bitrate_kbps * BITRATE_TOLERANCE_RATIO)
    )


async def _run(
    command: Sequence[str], *, timeout_seconds: float, failure: DownloadFailureCode
) -> str:
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        async with asyncio.timeout(timeout_seconds):
            stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        if process is not None and process.returncode is None:
            process.terminate()
            await asyncio.shield(process.wait())
        raise
    except TimeoutError as exc:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        raise MediaOperationError(failure) from exc
    except OSError as exc:
        raise MediaOperationError(failure) from exc
    if process.returncode != 0:
        if b"no space left on device" in stderr.lower():
            raise MediaOperationError(DownloadFailureCode.TEMP_STORAGE_UNAVAILABLE)
        raise MediaOperationError(failure)
    return stdout.decode("utf-8", errors="strict")


def _owned_path(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise MediaOperationError(DownloadFailureCode.INVALID_PLAN)
    return resolved


def _owned_file(path: Path, root: Path) -> Path:
    return _owned_path(path, root)


def _is_partial(path: Path) -> bool:
    return any(suffix in {".part", ".partial", ".tmp"} for suffix in path.suffixes)


def _validate_probe_target(path: Path) -> None:
    if not path.is_file() or path.stat().st_size <= 0 or _is_partial(path):
        raise MediaOperationError(DownloadFailureCode.MEDIA_PROBE_FAILED)


def _codec(value: object) -> NativeCodec:
    aliases = {
        "mp3": NativeCodec.MP3,
        "aac": NativeCodec.AAC,
        "flac": NativeCodec.FLAC,
        "vorbis": NativeCodec.VORBIS,
        "opus": NativeCodec.OPUS,
    }
    return aliases.get(str(value).lower(), NativeCodec.OTHER)


def _container(value: object, codec: NativeCodec) -> NativeContainer:
    names = {part.strip().lower() for part in str(value).split(",")}
    if "mp3" in names:
        return NativeContainer.MP3
    if "flac" in names:
        return NativeContainer.FLAC
    if names & {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}:
        return NativeContainer.M4A
    if "ogg" in names or codec is NativeCodec.VORBIS:
        return NativeContainer.OGG
    if "webm" in names:
        return NativeContainer.WEBM
    return NativeContainer.OTHER


def _positive_int(value: object) -> int | None:
    try:
        result = int(str(value))
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _kbps(value: object) -> int | None:
    raw = _positive_int(value)
    return round(raw / 1000) if raw is not None else None


def _duration_ms(value: object) -> int | None:
    try:
        result = round(float(str(value)) * 1000)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None
