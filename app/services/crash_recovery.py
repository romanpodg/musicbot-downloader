"""Deterministic, side-effect-free startup reconciliation."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.core.enums import QueueErrorCode
from app.services.artifacts import ArtifactPathError
from app.services.operational_audit import OperationalAuditService, RecoveryAuditDetails
from app.services.queues import UploadQueueService
from app.services.singleflight import SingleFlightService
from app.services.telegram_album_coordinator import (
    ALBUM_ITEM_MAX_ATTEMPTS,
    TelegramAlbumCoordinator,
)
from app.services.workers import DOWNLOAD_JOB_MAX_ATTEMPTS, UPLOAD_JOB_MAX_ATTEMPTS
from app.storage import Database
from app.storage.models.base import utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CrashRecoverySummary:
    download_jobs_recovered: int = 0
    upload_jobs_recovered: int = 0
    upload_artifacts_failed: int = 0
    uploads_recovered_from_cache: int = 0
    flights_reconciled: int = 0
    deliveries_recovered: int = 0
    album_items_recovered: int = 0
    album_requests_reconciled: int = 0


@dataclass(frozen=True, slots=True)
class RecoveryInspection:
    snapshot_at: datetime
    expired_download_leases: int = 0
    expired_upload_leases: int = 0
    invalid_or_missing_upload_artifacts: int = 0
    exact_cache_rescue_candidates: int = 0
    stale_singleflight_candidates: int = 0
    expired_delivery_leases: int = 0
    expired_album_item_leases: int = 0
    album_aggregate_reconciliation_candidates: int = 0


class CrashRecoveryService:
    """Compose existing durable recovery primitives before any worker claims."""

    def __init__(
        self,
        database: Database,
        upload_queue: UploadQueueService,
        singleflight: SingleFlightService,
        album_coordinator: TelegramAlbumCoordinator,
        *,
        telegram_bot_id: int,
        delivery_max_attempts: int,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._database = database
        self._upload_queue = upload_queue
        self._singleflight = singleflight
        self._album_coordinator = album_coordinator
        self._telegram_bot_id = telegram_bot_id
        self._delivery_max_attempts = delivery_max_attempts
        self._clock = clock
        self._audit = OperationalAuditService(database)

    async def inspect(self) -> RecoveryInspection:
        """Return a read-only snapshot using the same recovery predicates."""

        now = self._clock()
        async with self._database.transaction() as repositories:
            download_leases = await repositories.download_jobs.count_expired_leases(now)
            upload_leases = await repositories.upload_jobs.count_expired_leases(now)
            active_uploads = await repositories.upload_jobs.list_nonterminal()
            stale_flights = await repositories.singleflight.count_reconciliation_candidates()
            delivery_leases = await repositories.telegram_delivery.count_expired_leases(now)
            album_leases = await repositories.telegram_album.count_expired_leases(now)
            aggregate_candidates = (
                await repositories.telegram_album.count_aggregate_reconciliation_candidates()
            )

        invalid_artifacts = 0
        cache_rescues = 0
        for upload in active_uploads:
            try:
                self._upload_queue.validate_artifact_reference(
                    upload.artifact_job_id, upload.artifact_path
                )
            except (ArtifactPathError, OSError):
                invalid_artifacts += 1
                continue
            async with self._database.transaction() as repositories:
                cached = await repositories.telegram_cache.get_active(
                    telegram_bot_id=self._telegram_bot_id,
                    track_id=upload.track_id,
                    quality_profile=upload.quality_profile,
                )
            if cached is not None:
                cache_rescues += 1
                continue
            try:
                self._upload_queue.validate_artifact(
                    upload.artifact_job_id,
                    upload.artifact_path,
                    expected_size=upload.file_size_bytes,
                )
            except (ArtifactPathError, OSError):
                invalid_artifacts += 1

        return RecoveryInspection(
            snapshot_at=now,
            expired_download_leases=download_leases,
            expired_upload_leases=upload_leases,
            invalid_or_missing_upload_artifacts=invalid_artifacts,
            exact_cache_rescue_candidates=cache_rescues,
            stale_singleflight_candidates=stale_flights,
            expired_delivery_leases=delivery_leases,
            expired_album_item_leases=album_leases,
            album_aggregate_reconciliation_candidates=aggregate_candidates,
        )

    async def recover_startup(self, *, manual: bool = False) -> CrashRecoverySummary:
        logger.info("crash_recovery_started")
        now = self._clock()
        async with self._database.transaction() as repositories:
            download_recovered = await repositories.download_jobs.recover_expired(
                now, DOWNLOAD_JOB_MAX_ATTEMPTS
            )
            upload_recovery = await repositories.upload_jobs.recover_expired(
                now, UPLOAD_JOB_MAX_ATTEMPTS
            )
            deliveries_recovered = await repositories.telegram_delivery.recover_expired(
                now=now, max_attempts=self._delivery_max_attempts
            )
            album_items_recovered = await repositories.telegram_album.recover_expired(
                now=now, max_attempts=ALBUM_ITEM_MAX_ATTEMPTS
            )
            active_uploads = await repositories.upload_jobs.list_nonterminal()

        for artifact in upload_recovery.terminal_artifacts:
            self._upload_queue.release_owned(*artifact)

        artifact_failures = 0
        cache_rescues = 0
        direct_reconciliations = 0
        for upload in active_uploads:
            try:
                self._upload_queue.validate_artifact_reference(
                    upload.artifact_job_id, upload.artifact_path
                )
            except (ArtifactPathError, OSError):
                reconciled = await self._fail_artifact(
                    upload.id,
                    upload.download_job_id,
                    now,
                    QueueErrorCode.UPLOAD_ARTIFACT_INVALID.value,
                )
                artifact_failures += 1
                direct_reconciliations += int(reconciled)
                logger.warning("upload_artifact_invalid", extra={"job_id": upload.id})
                continue

            async with self._database.transaction() as repositories:
                cached = await repositories.telegram_cache.get_active(
                    telegram_bot_id=self._telegram_bot_id,
                    track_id=upload.track_id,
                    quality_profile=upload.quality_profile,
                )
                if cached is not None:
                    rescued = await repositories.upload_jobs.succeed_recovered(
                        job_id=upload.id, now=now
                    )
                    reconciled = (
                        await repositories.singleflight.reconcile_download_job(
                            upload.download_job_id, now
                        )
                        if rescued
                        else False
                    )
                else:
                    rescued = False
                    reconciled = False
            if rescued:
                cache_rescues += 1
                direct_reconciliations += int(reconciled)
                self._upload_queue.release_owned(upload.artifact_job_id, upload.artifact_path)
                logger.info("upload_recovered_from_cache", extra={"job_id": upload.id})
                continue

            error_code: str | None = None
            try:
                self._upload_queue.validate_artifact(
                    upload.artifact_job_id,
                    upload.artifact_path,
                    expected_size=upload.file_size_bytes,
                )
            except FileNotFoundError:
                error_code = QueueErrorCode.UPLOAD_ARTIFACT_MISSING.value
            except (ArtifactPathError, OSError):
                error_code = QueueErrorCode.UPLOAD_ARTIFACT_INVALID.value
            if error_code is None:
                continue

            reconciled = await self._fail_artifact(
                upload.id, upload.download_job_id, now, error_code
            )
            artifact_failures += 1
            direct_reconciliations += int(reconciled)
            logger.warning(
                "upload_artifact_missing"
                if error_code == QueueErrorCode.UPLOAD_ARTIFACT_MISSING.value
                else "upload_artifact_invalid",
                extra={"job_id": upload.id},
            )

        flights_reconciled = direct_reconciliations + await self._singleflight.reconcile()
        album_requests_reconciled = await self._album_coordinator.reconcile(notify=False)
        summary = CrashRecoverySummary(
            download_jobs_recovered=download_recovered,
            upload_jobs_recovered=upload_recovery.recovered,
            upload_artifacts_failed=artifact_failures,
            uploads_recovered_from_cache=cache_rescues,
            flights_reconciled=flights_reconciled,
            deliveries_recovered=deliveries_recovered,
            album_items_recovered=album_items_recovered,
            album_requests_reconciled=album_requests_reconciled,
        )
        logger.info(
            "crash_recovery_completed",
            extra={
                "download_jobs_recovered": summary.download_jobs_recovered,
                "upload_jobs_recovered": summary.upload_jobs_recovered,
                "upload_artifacts_failed": summary.upload_artifacts_failed,
                "uploads_recovered_from_cache": summary.uploads_recovered_from_cache,
                "flights_reconciled": summary.flights_reconciled,
                "deliveries_recovered": summary.deliveries_recovered,
                "album_items_recovered": summary.album_items_recovered,
                "album_requests_reconciled": summary.album_requests_reconciled,
            },
        )
        details = RecoveryAuditDetails(
            download_jobs_recovered=summary.download_jobs_recovered,
            upload_jobs_recovered=summary.upload_jobs_recovered,
            upload_artifacts_failed=summary.upload_artifacts_failed,
            uploads_recovered_from_cache=summary.uploads_recovered_from_cache,
            flights_reconciled=summary.flights_reconciled,
            deliveries_recovered=summary.deliveries_recovered,
            album_items_recovered=summary.album_items_recovered,
            album_requests_reconciled=summary.album_requests_reconciled,
        )
        if manual:
            await self._audit.append_recovery(details, manual=True)
        else:
            try:
                await self._audit.append_recovery(details, manual=False)
            except Exception:
                # Recovery spans several deliberate transactions. Once it has completed,
                # an audit outage must not trigger a destructive recovery replay.
                logger.exception("crash_recovery_audit_failed")
        return summary

    async def _fail_artifact(
        self,
        upload_job_id: int,
        download_job_id: int,
        now: datetime,
        error_code: str,
    ) -> bool:
        async with self._database.transaction() as repositories:
            failed = await repositories.upload_jobs.fail_recovered(
                job_id=upload_job_id, now=now, error_code=error_code
            )
            if not failed:
                return False
            return await repositories.singleflight.reconcile_download_job(download_job_id, now)
