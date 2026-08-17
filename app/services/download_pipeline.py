"""Execute fresh Stage 5 plans into validated temporary audio artifacts."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.core.enums import (
    DownloadAttemptStatus,
    DownloadFailureCode,
    DownloadPlanOperation,
    DownloadPlanReadiness,
    MusicProviderName,
    NativeCodec,
    NativeContainer,
    ProviderRuntimeStatus,
    QualityProfile,
    QualityResolutionStatus,
    SourceValidationConfidence,
)
from app.core.exceptions import (
    DownloadPipelineError,
    MetadataUnavailable,
    ProviderAuthenticationError,
    ProviderOperationTimeout,
    ProviderUnavailable,
)
from app.core.models import (
    DownloadAttempt,
    DownloadPlan,
    DownloadResult,
    PreparedSourceMedia,
    ProviderSourceCheck,
    QualityResolutionResult,
)
from app.core.quality import QUALITY_OUTPUTS
from app.services.artifacts import ArtifactPathError, DownloadArtifactManager
from app.services.media import (
    MediaOperationError,
    MediaProbe,
    Transcoder,
    media_satisfies_requirement,
    output_satisfies_specification,
)
from app.storage import Database

logger = logging.getLogger(__name__)


class QualityResolverBoundary(Protocol):
    async def resolve(
        self, track_id: int, quality_profile: QualityProfile
    ) -> QualityResolutionResult: ...


class NativeDownloadBoundary(Protocol):
    async def check_source(
        self, provider: MusicProviderName, provider_track_id: str
    ) -> ProviderSourceCheck: ...

    async def prepare_source(
        self, provider: MusicProviderName, provider_track_id: str
    ) -> PreparedSourceMedia | None: ...

    async def download_source(
        self,
        provider: MusicProviderName,
        provider_track_id: str,
        job_id: str,
        plan_rank: int,
        *,
        timeout_seconds: float,
    ) -> PreparedSourceMedia: ...


class DownloadPipeline:
    """One execution pipeline; queueing and persistent jobs intentionally live elsewhere."""

    def __init__(
        self,
        database: Database,
        quality_resolver: QualityResolverBoundary,
        provider: NativeDownloadBoundary,
        artifacts: DownloadArtifactManager,
        probe: MediaProbe,
        transcoder: Transcoder,
        *,
        download_timeout: float = 600,
    ) -> None:
        self._database = database
        self._quality_resolver = quality_resolver
        self._provider = provider
        self._artifacts = artifacts
        self._probe = probe
        self._transcoder = transcoder
        self._download_timeout = download_timeout

    async def download(self, track_id: int, quality_profile: QualityProfile) -> DownloadResult:
        resolution = await self._quality_resolver.resolve(track_id, quality_profile)
        if not resolution.plans:
            code = (
                DownloadFailureCode.NO_AVAILABLE_PROVIDER
                if resolution.status is QualityResolutionStatus.NO_AVAILABLE_PROVIDER
                else DownloadFailureCode.SOURCE_REQUIREMENT_MISMATCH
            )
            raise DownloadPipelineError(code)
        metadata, expected_duration = await self._track_metadata(track_id)
        try:
            job_id, _ = self._artifacts.create_job()
        except OSError as exc:
            raise DownloadPipelineError(DownloadFailureCode.TEMP_STORAGE_UNAVAILABLE) from exc

        attempts: list[DownloadAttempt] = []
        last_code = DownloadFailureCode.NO_AVAILABLE_PROVIDER
        try:
            for plan_rank, plan in enumerate(resolution.plans, 1):
                try:
                    self._validate_plan(plan, track_id, quality_profile)
                    self._artifacts.attempt_path(job_id, plan_rank)
                    runtime = await self._provider.check_source(
                        plan.provider, plan.provider_track_id
                    )
                    if runtime.status is not ProviderRuntimeStatus.AVAILABLE:
                        code = _runtime_failure(runtime.status)
                        attempts.append(
                            _attempt(plan, plan_rank, DownloadAttemptStatus.SKIPPED, code)
                        )
                        last_code = code
                        self._artifacts.cleanup_attempt(job_id, plan_rank)
                        continue
                    if plan.readiness is DownloadPlanReadiness.REQUIRES_PREFLIGHT:
                        prepared = await self._provider.prepare_source(
                            plan.provider, plan.provider_track_id
                        )
                        if prepared is None or not media_satisfies_requirement(
                            prepared, plan.source_expectation
                        ):
                            code = DownloadFailureCode.SOURCE_REQUIREMENT_MISMATCH
                            attempts.append(
                                _attempt(plan, plan_rank, DownloadAttemptStatus.FAILED, code)
                            )
                            last_code = code
                            self._artifacts.cleanup_attempt(job_id, plan_rank)
                            continue
                    declared = await self._provider.download_source(
                        plan.provider,
                        plan.provider_track_id,
                        job_id,
                        plan_rank,
                        timeout_seconds=self._download_timeout,
                    )
                    if declared.file_path is None:
                        raise MediaOperationError(DownloadFailureCode.SOURCE_VALIDATION_FAILED)
                    self._artifacts.ensure_owned(declared.file_path, job_id)
                    source = await self._probe.probe(
                        declared.file_path,
                        provider=plan.provider,
                        provider_track_id=plan.provider_track_id,
                        native_encoded=declared.native_encoded,
                        provider_decrypted=declared.provider_decrypted,
                        upstream_quality_transcoded=declared.upstream_quality_transcoded,
                    )
                    source = replace(
                        source,
                        validation_confidence=SourceValidationConfidence.PROBED_WITH_NATIVE_PROVENANCE,
                    )
                    if not media_satisfies_requirement(source, plan.source_expectation):
                        raise MediaOperationError(DownloadFailureCode.SOURCE_REQUIREMENT_MISMATCH)
                    final_path = await self._execute_plan(job_id, plan, source, metadata)
                    output = await self._probe.probe(
                        final_path,
                        provider=plan.provider,
                        provider_track_id=plan.provider_track_id,
                        native_encoded=plan.operation is DownloadPlanOperation.DIRECT,
                        provider_decrypted=source.provider_decrypted,
                        upstream_quality_transcoded=False,
                    )
                    output = replace(
                        output,
                        validation_confidence=SourceValidationConfidence.PROBED_WITH_NATIVE_PROVENANCE,
                    )
                    if not output_satisfies_specification(
                        output, plan.output_specification, expected_duration
                    ):
                        final_path.unlink(missing_ok=True)
                        raise MediaOperationError(DownloadFailureCode.OUTPUT_VALIDATION_FAILED)
                    attempts.append(
                        _attempt(plan, plan_rank, DownloadAttemptStatus.SUCCEEDED, None)
                    )
                    self._artifacts.cleanup_attempt(job_id, plan_rank)
                    source = replace(source, file_path=None)
                    result = DownloadResult(
                        job_id=job_id,
                        track_id=track_id,
                        requested_profile=quality_profile,
                        track_source_id=plan.track_source_id,
                        provider=plan.provider,
                        provider_track_id=plan.provider_track_id,
                        operation=plan.operation,
                        plan_readiness=plan.readiness,
                        source_media=source,
                        output_media=output,
                        file_path=final_path,
                        file_size=final_path.stat().st_size,
                        transcoded=plan.operation is DownloadPlanOperation.TRANSCODE,
                        fallback_index=plan_rank - 1,
                        attempts=tuple(attempts),
                        created_at=datetime.now(UTC),
                        encoder=_encoder_for(plan),
                    )
                    self._log_attempt(job_id, plan, plan_rank, "success")
                    return result
                except asyncio.CancelledError:
                    raise
                except DownloadPipelineError:
                    raise
                except ProviderAuthenticationError:
                    last_code = DownloadFailureCode.AUTH_REQUIRED
                except ProviderOperationTimeout:
                    last_code = DownloadFailureCode.DOWNLOAD_TIMEOUT
                except ProviderUnavailable:
                    last_code = DownloadFailureCode.PROVIDER_UNAVAILABLE
                except MetadataUnavailable:
                    last_code = DownloadFailureCode.SOURCE_UNAVAILABLE
                except MediaOperationError as exc:
                    last_code = exc.code
                    if exc.code in {
                        DownloadFailureCode.MEDIA_PROBE_UNAVAILABLE,
                        DownloadFailureCode.INVALID_PLAN,
                    }:
                        raise DownloadPipelineError(exc.code, tuple(attempts)) from exc
                except (ArtifactPathError, PermissionError, OSError) as exc:
                    raise DownloadPipelineError(
                        DownloadFailureCode.TEMP_STORAGE_UNAVAILABLE, tuple(attempts)
                    ) from exc
                attempts.append(_attempt(plan, plan_rank, DownloadAttemptStatus.FAILED, last_code))
                self._log_attempt(job_id, plan, plan_rank, last_code.value)
                self._artifacts.cleanup_attempt(job_id, plan_rank)
            if attempts and all(
                item.failure_code is DownloadFailureCode.AUTH_REQUIRED for item in attempts
            ):
                last_code = DownloadFailureCode.NO_AVAILABLE_PROVIDER
            raise DownloadPipelineError(last_code, tuple(attempts))
        except BaseException:
            self._artifacts.release(job_id)
            raise

    async def _execute_plan(
        self,
        job_id: str,
        plan: DownloadPlan,
        source: PreparedSourceMedia,
        metadata: dict[str, str],
    ) -> Path:
        if source.file_path is None:
            raise MediaOperationError(DownloadFailureCode.SOURCE_VALIDATION_FAILED)
        extension = _output_extension(plan, source.container)
        final_path = self._artifacts.final_path(job_id, extension)
        if plan.operation is DownloadPlanOperation.DIRECT:
            os.replace(source.file_path, final_path)
            await self._transcoder.tag_copy(final_path, metadata)
            return final_path
        if source.lossless is not True:
            raise MediaOperationError(DownloadFailureCode.SOURCE_REQUIREMENT_MISMATCH)
        partial = final_path.with_suffix(final_path.suffix + ".partial")
        try:
            await self._transcoder.transcode(
                source.file_path, partial, plan.output_specification, metadata
            )
            os.replace(partial, final_path)
        finally:
            partial.unlink(missing_ok=True)
        return final_path

    @staticmethod
    def _validate_plan(plan: DownloadPlan, track_id: int, profile: QualityProfile) -> None:
        if (
            plan.track_id != track_id
            or plan.requested_profile is not profile
            or plan.output_specification != QUALITY_OUTPUTS[profile]
        ):
            raise DownloadPipelineError(DownloadFailureCode.INVALID_PLAN)
        if plan.operation is DownloadPlanOperation.TRANSCODE:
            if (
                plan.source_expectation.required_lossless is not True
                or plan.output_specification.lossless
            ):
                raise DownloadPipelineError(DownloadFailureCode.INVALID_PLAN)
        elif plan.operation is DownloadPlanOperation.DIRECT:
            source = plan.source_expectation
            output = plan.output_specification
            exact_lossy = (
                source.required_codec is output.codec
                and source.required_bitrate_kbps == output.bitrate_kbps
            )
            if not (output.lossless and source.required_lossless is True) and not exact_lossy:
                raise DownloadPipelineError(DownloadFailureCode.INVALID_PLAN)

    async def _track_metadata(self, track_id: int) -> tuple[dict[str, str], int | None]:
        async with self._database.transaction() as repositories:
            track = await repositories.tracks.get_track_by_id(track_id)
            if track is None:
                from app.core.exceptions import TrackNotFound

                raise TrackNotFound()
            values = {
                key: value
                for key, value in {
                    "title": track.title,
                    "artist": track.artist,
                    "album": track.album,
                    "isrc": track.isrc,
                }.items()
                if value
            }
            return values, track.duration_ms

    @staticmethod
    def _log_attempt(job_id: str, plan: DownloadPlan, plan_rank: int, result: str) -> None:
        logger.info(
            "Download plan attempt completed",
            extra={
                "job_id": job_id,
                "track_id": plan.track_id,
                "track_source_id": plan.track_source_id,
                "provider": plan.provider.value,
                "requested_profile": plan.requested_profile.value,
                "plan_rank": plan_rank,
                "operation": plan.operation.value,
                "readiness": plan.readiness.value,
                "attempt_result": result,
            },
        )


def _attempt(
    plan: DownloadPlan,
    rank: int,
    status: DownloadAttemptStatus,
    failure: DownloadFailureCode | None,
) -> DownloadAttempt:
    return DownloadAttempt(plan.provider, plan.track_source_id, rank, status, failure)


def _runtime_failure(status: ProviderRuntimeStatus) -> DownloadFailureCode:
    if status is ProviderRuntimeStatus.AUTH_REQUIRED:
        return DownloadFailureCode.AUTH_REQUIRED
    if status is ProviderRuntimeStatus.SOURCE_UNAVAILABLE:
        return DownloadFailureCode.SOURCE_UNAVAILABLE
    return DownloadFailureCode.PROVIDER_UNAVAILABLE


def _output_extension(plan: DownloadPlan, source_container: NativeContainer | None) -> str:
    if plan.output_specification.container is NativeContainer.MP3:
        return "mp3"
    if plan.output_specification.container is NativeContainer.M4A:
        return "m4a"
    if plan.output_specification.lossless and source_container is NativeContainer.FLAC:
        return "flac"
    raise MediaOperationError(DownloadFailureCode.INVALID_PLAN)


def _encoder_for(plan: DownloadPlan) -> str | None:
    if plan.operation is DownloadPlanOperation.DIRECT:
        return None
    if plan.output_specification.codec is NativeCodec.MP3:
        return "libmp3lame"
    if plan.output_specification.codec is NativeCodec.AAC:
        return "aac"
    return None
