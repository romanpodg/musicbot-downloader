from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.enums import (
    DownloadFailureCode,
    DownloadPlanOperation,
    DownloadPlanReadiness,
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    QualityProfile,
)
from app.core.exceptions import DownloadPipelineError
from app.core.models import DownloadResult, PreparedSourceMedia, ProviderMediaCapabilities
from app.core.provider_resolution import CanonicalMediaIdentity, ProviderCandidate, match_media
from app.services.artifacts import DownloadArtifactManager


@dataclass
class _Candidates:
    values: tuple[ProviderCandidate, ...]

    async def resolve(self, identity, **kwargs):  # type: ignore[no-untyped-def]
        return self.values


@dataclass
class _Accounts:
    values: dict[MusicProviderName, tuple[str, ...]]

    async def list_provider_accounts(self, provider: MusicProviderName) -> tuple[str, ...]:
        return self.values.get(provider, ())


class _ExactPipeline:
    def __init__(
        self,
        artifacts: DownloadArtifactManager,
        outcomes: dict[tuple[MusicProviderName, str | None], DownloadFailureCode | None],
        source_id: int,
    ) -> None:
        self._artifacts = artifacts
        self._outcomes = outcomes
        self._source_id = source_id
        self.calls: list[tuple[MusicProviderName, str | None]] = []

    async def download_selected(
        self,
        track_id: int,
        quality_profile: QualityProfile,
        *,
        provider: MusicProviderName,
        provider_media_id: str,
        account_id: str | None = None,
    ) -> DownloadResult:
        self.calls.append((provider, account_id))
        failure = self._outcomes[(provider, account_id)]
        if failure is not None:
            raise DownloadPipelineError(failure)
        artifact_job_id, _ = self._artifacts.create_job()
        path = self._artifacts.final_path(artifact_job_id, "mp3")
        path.write_bytes(b"stage25-audio")
        media = PreparedSourceMedia(
            provider,
            provider_media_id,
            codec=NativeCodec.MP3,
            container=NativeContainer.MP3,
            bitrate_kbps=320,
            lossless=False,
            file_path=path,
        )
        return DownloadResult(
            artifact_job_id,
            track_id,
            quality_profile,
            self._source_id,
            provider,
            provider_media_id,
            DownloadPlanOperation.DIRECT,
            DownloadPlanReadiness.CONFIRMED,
            media,
            media,
            path,
            path.stat().st_size,
            False,
            0,
            (),
            datetime.now(UTC),
        )


def _candidate(provider: MusicProviderName, media_id: str) -> ProviderCandidate:
    identity = CanonicalMediaIdentity.from_values(
        title="Stage 25", artist="Executor", isrc="USABC1234567"
    )
    return ProviderCandidate(
        provider,
        media_id,
        identity,
        match_media(identity, identity),
        ProviderMediaCapabilities(supports_lossy=True),
    )
