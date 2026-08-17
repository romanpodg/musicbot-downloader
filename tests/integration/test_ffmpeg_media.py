from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.core.enums import MusicProviderName, NativeCodec, NativeContainer, QualityProfile
from app.core.quality import QUALITY_OUTPUTS
from app.services.media import MediaProbe, Transcoder, output_satisfies_specification

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")


def _generate_flac(source: Path) -> None:
    subprocess.run(
        [
            str(_FFMPEG),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:a",
            "flac",
            str(source),
        ],
        check=True,
    )


@pytest.mark.skipif(not _FFMPEG or not _FFPROBE, reason="system FFmpeg/ffprobe unavailable")
@pytest.mark.asyncio
async def test_synthetic_flac_transcodes_to_valid_mp3_and_aac(tmp_path: Path) -> None:
    source = tmp_path / "tone.flac"
    _generate_flac(source)
    transcoder = Transcoder(tmp_path, str(_FFMPEG), timeout=30)
    probe = MediaProbe(tmp_path, str(_FFPROBE))

    for profile, extension in (
        (QualityProfile.MP3_128, "mp3"),
        (QualityProfile.MP3_320, "mp3"),
        (QualityProfile.AAC_256, "m4a"),
    ):
        final = tmp_path / f"final-{profile.value}.{extension}"
        partial = final.with_suffix(final.suffix + ".partial")
        await transcoder.transcode(source, partial, QUALITY_OUTPUTS[profile], {"title": "Tone"})
        partial.replace(final)
        media = await probe.probe(
            final,
            provider=MusicProviderName.QOBUZ,
            provider_track_id="synthetic",
            native_encoded=False,
        )
        assert media.codec is QUALITY_OUTPUTS[profile].codec
        assert media.container is QUALITY_OUTPUTS[profile].container
        assert output_satisfies_specification(media, QUALITY_OUTPUTS[profile], 1000)

    source_media = await probe.probe(
        source,
        provider=MusicProviderName.QOBUZ,
        provider_track_id="synthetic",
        native_encoded=True,
    )
    assert source_media.codec is NativeCodec.FLAC
    assert source_media.container is NativeContainer.FLAC
    assert source_media.lossless is True
