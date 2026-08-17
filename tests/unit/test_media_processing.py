from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from app.core.enums import MusicProviderName, NativeCodec, NativeContainer, QualityProfile
from app.core.models import PreparedSourceMedia, SourceMediaRequirement
from app.core.quality import QUALITY_OUTPUTS
from app.services.media import (
    MediaOperationError,
    MediaProbe,
    Transcoder,
    media_satisfies_requirement,
    output_satisfies_specification,
)


@pytest.mark.parametrize(
    ("profile", "encoder", "bitrate", "muxer"),
    [
        (QualityProfile.MP3_128, "libmp3lame", "128k", "mp3"),
        (QualityProfile.MP3_320, "libmp3lame", "320k", "mp3"),
        (QualityProfile.AAC_256, "aac", "256k", "ipod"),
    ],
)
def test_transcoder_builds_exact_argv_without_shell(
    tmp_path: Path,
    profile: QualityProfile,
    encoder: str,
    bitrate: str,
    muxer: str,
) -> None:
    source = tmp_path / "source.flac"
    output = tmp_path / "final.partial"
    source.write_bytes(b"fixture")
    command = Transcoder(tmp_path, "ffmpeg").command(
        source,
        output,
        QUALITY_OUTPUTS[profile],
        {"title": "A; $(unsafe)", "artist": "Artist"},
    )

    assert command[0] == "ffmpeg"
    assert command[command.index("-c:a") + 1] == encoder
    assert command[command.index("-b:a") + 1] == bitrate
    assert command[command.index("-f") + 1] == muxer
    assert command[-1] == str(output.resolve())
    assert "title=A; $(unsafe)" in command


def test_lossy_media_never_satisfies_lossless_requirement() -> None:
    media = PreparedSourceMedia(
        MusicProviderName.TIDAL,
        "track",
        codec=NativeCodec.AAC,
        container=NativeContainer.M4A,
        bitrate_kbps=256,
        lossless=False,
    )
    requirement = SourceMediaRequirement(required_lossless=True)
    assert media_satisfies_requirement(media, requirement) is False


def test_mp3_256_does_not_satisfy_exact_mp3_320_source_requirement() -> None:
    media = PreparedSourceMedia(
        MusicProviderName.DEEZER,
        "track",
        codec=NativeCodec.MP3,
        container=NativeContainer.MP3,
        bitrate_kbps=256,
        lossless=False,
    )
    requirement = SourceMediaRequirement(required_codec=NativeCodec.MP3, required_bitrate_kbps=320)
    assert media_satisfies_requirement(media, requirement) is False


@pytest.mark.parametrize(
    "media",
    [
        PreparedSourceMedia(
            MusicProviderName.DEEZER,
            "x",
            codec=NativeCodec.MP3,
            container=NativeContainer.MP3,
            bitrate_kbps=128,
            duration_ms=10_000,
            file_path=Path("missing"),
        ),
        PreparedSourceMedia(
            MusicProviderName.DEEZER,
            "x",
            codec=NativeCodec.AAC,
            container=NativeContainer.M4A,
            bitrate_kbps=320,
            duration_ms=10_000,
            file_path=Path("missing"),
        ),
    ],
)
def test_missing_or_wrong_output_fails(media: PreparedSourceMedia) -> None:
    assert not output_satisfies_specification(
        media, QUALITY_OUTPUTS[QualityProfile.MP3_320], 10_000
    )


def test_invalid_transcode_contract_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.flac"
    source.write_bytes(b"fixture")
    with pytest.raises(MediaOperationError):
        Transcoder(tmp_path).command(
            source, tmp_path / "bad.partial", QUALITY_OUTPUTS[QualityProfile.LOSSLESS], {}
        )


@pytest.mark.parametrize(
    ("profile", "codec", "container", "bitrate", "lossless", "expected"),
    [
        (QualityProfile.MP3_320, NativeCodec.MP3, NativeContainer.MP3, 320, False, True),
        (QualityProfile.MP3_320, NativeCodec.MP3, NativeContainer.MP3, 128, False, False),
        (QualityProfile.AAC_256, NativeCodec.AAC, NativeContainer.M4A, 256, False, True),
        (QualityProfile.LOSSLESS, NativeCodec.FLAC, NativeContainer.FLAC, None, True, True),
    ],
)
def test_direct_output_contracts(
    tmp_path: Path,
    profile: QualityProfile,
    codec: NativeCodec,
    container: NativeContainer,
    bitrate: int | None,
    lossless: bool,
    expected: bool,
) -> None:
    path = tmp_path / "audio.bin"
    path.write_bytes(b"audio")
    media = PreparedSourceMedia(
        MusicProviderName.DEEZER,
        "x",
        codec,
        container,
        bitrate,
        duration_ms=10_000,
        lossless=lossless,
        file_path=path,
    )
    assert output_satisfies_specification(media, QUALITY_OUTPUTS[profile], 10_000) is expected


def test_gross_duration_mismatch_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"audio")
    media = PreparedSourceMedia(
        MusicProviderName.DEEZER,
        "x",
        NativeCodec.MP3,
        NativeContainer.MP3,
        320,
        duration_ms=20_000,
        file_path=path,
    )
    assert not output_satisfies_specification(
        media, QUALITY_OUTPUTS[QualityProfile.MP3_320], 10_000
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "content"), [("zero.mp3", b""), ("audio.mp3.partial", b"x")])
async def test_probe_rejects_empty_and_partial_files(
    tmp_path: Path, name: str, content: bytes
) -> None:
    path = tmp_path / name
    path.write_bytes(content)
    with pytest.raises(MediaOperationError):
        await MediaProbe(tmp_path, sys.executable).probe(
            path,
            provider=MusicProviderName.DEEZER,
            provider_track_id="x",
            native_encoded=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["not json", '{"streams":[],"format":{}}'])
async def test_probe_rejects_invalid_response_or_missing_audio_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: str
) -> None:
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"audio")

    async def fake_run(*args: Any, **kwargs: Any) -> str:
        return response

    monkeypatch.setattr("app.services.media._run", fake_run)
    with pytest.raises(MediaOperationError):
        await MediaProbe(tmp_path, sys.executable).probe(
            path,
            provider=MusicProviderName.DEEZER,
            provider_track_id="x",
            native_encoded=True,
        )
