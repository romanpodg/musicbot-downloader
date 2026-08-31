"""Stage 21 lifecycle domain service over durable SQLite records."""

from __future__ import annotations

import logging
import random
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.core.delivery_targets import DeliveryTargetType
from app.core.download import DownloadDeliveryTarget, DownloadRequest
from app.core.download_preferences import (
    DownloadProfileResolver,
    UserDownloadPreferences,
)
from app.core.enums import (
    DownloadFailureCode,
    DownloadJobStatus,
    DownloadPhase,
    DownloadSourceType,
    MusicProviderName,
    QualityPreference,
)
from app.core.models import ProviderCapabilities
from app.providers.onthespot.capabilities import ONTHESPOT_CAPABILITIES
from app.storage import Database
from app.storage.models.base import utc_now
from app.storage.models.download_lifecycle import (
    DownloadDelivery,
    DownloadLifecycleJob,
    DownloadRequestRecord,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LifecycleAdmission:
    request: DownloadRequestRecord
    job: DownloadLifecycleJob
    delivery: DownloadDelivery


class DownloadLifecycleService:
    """Owns lifecycle transitions; callers cannot freely mutate status fields."""

    def __init__(
        self,
        database: Database,
        *,
        max_attempts: int = 3,
        lease_seconds: float = 60.0,
        clock: Callable[[], datetime] = utc_now,
        random_source: Callable[[], float] | None = None,
        profile_resolver: DownloadProfileResolver | None = None,
        capability_provider: Callable[[MusicProviderName], ProviderCapabilities] | None = None,
    ) -> None:
        if max_attempts < 1 or lease_seconds <= 0:
            raise ValueError("invalid lifecycle limits")
        self.database = database
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds
        self.clock = clock
        self._random = random_source or random.random
        self._profile_resolver = profile_resolver or DownloadProfileResolver()
        self._capability_provider = capability_provider

    async def admit(
        self,
        *,
        confirmation_id: str,
        request: DownloadRequest,
        canonical_track_id: int,
        target: DownloadDeliveryTarget,
        source_type: DownloadSourceType = DownloadSourceType.RECOGNITION_RESULT,
        initial_status: DownloadJobStatus = DownloadJobStatus.QUEUED,
        telegram_delivery_request_id: int | None = None,
    ) -> LifecycleAdmission:
        if target.delivery_target.target_type is not DeliveryTargetType.PRIVATE_USER:
            # Explicit CHAT/CHANNEL targets remain valid Stage 19 behavior; the
            # normal USER resolver supplies PRIVATE_USER before this boundary.
            pass
        now = self.clock()
        provider = request.recognized_track.provider
        async with self.database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(request.user_id)
            if user is None:
                raise ValueError("download user is not available")
            profile = request.effective_profile
            if profile is None:
                preferences = await repositories.download_preferences.get_effective(user.id)
                if request.options.quality_profile is not None:
                    requested = {
                        "LOSSLESS": QualityPreference.LOSSLESS,
                        "MP3_320": QualityPreference.HIGH,
                        "MP3_128": QualityPreference.STANDARD,
                        "AAC_256": QualityPreference.HIGH,
                    }[request.options.quality_profile.value]
                    preferences = UserDownloadPreferences(
                        user_id=user.id,
                        quality=requested,
                        format=preferences.format,
                        delivery_mode=preferences.delivery_mode,
                        embed_metadata=preferences.embed_metadata,
                        embed_cover=preferences.embed_cover,
                    )
                capabilities = (
                    self._capability_provider(provider)
                    if self._capability_provider is not None and provider is not None
                    else ONTHESPOT_CAPABILITIES.get(provider)
                )
                if capabilities is not None:
                    profile = self._profile_resolver.resolve(
                        preferences, capabilities, capabilities.media
                    )
            stored_request, job, delivery = await repositories.download_lifecycle.admit(
                requester_user_id=user.id,
                confirmation_id=confirmation_id,
                source_type=source_type.value,
                source_reference=str(canonical_track_id),
                provider=provider.value if provider is not None else None,
                provider_media_id=request.recognized_track.provider_track_id,
                media_title=request.recognized_track.title,
                media_artist=", ".join(artist.name for artist in request.recognized_track.artists),
                media_album=(
                    request.recognized_track.album.title if request.recognized_track.album else None
                ),
                replay_of_request_id=request.replay_of_request_id,
                delivery_target_type=target.delivery_target.target_type,
                delivery_target_id=target.delivery_target.chat_id,
                now=now,
                max_attempts=self.max_attempts,
                initial_status=initial_status,
                telegram_delivery_request_id=telegram_delivery_request_id,
                profile=profile,
            )
        logger.info(
            "download_lifecycle_admitted",
            extra={
                "job_id": job.id,
                "request_id": stored_request.id,
                "user_id": request.user_id,
                "status": job.status.value,
            },
        )
        return LifecycleAdmission(stored_request, job, delivery)

    async def claim(self, worker_id: str) -> DownloadLifecycleJob | None:
        now = self.clock()
        async with self.database.transaction() as repositories:
            return await repositories.download_lifecycle.claim(
                worker_id=worker_id,
                now=now,
                lease_expires_at=now + timedelta(seconds=self.lease_seconds),
            )

    async def queue(self, job_id: int) -> bool:
        """Re-enter the fair queue after admission or persisted retry time."""
        async with self.database.transaction() as repositories:
            return await repositories.download_lifecycle.queue(job_id, self.clock())

    async def start(self, worker_id: str) -> DownloadLifecycleJob | None:
        """Atomically acquire the next queued lifecycle job."""
        return await self.claim(worker_id)

    async def heartbeat(self, job_id: int, worker_id: str) -> bool:
        async with self.database.transaction() as repositories:
            return await repositories.download_lifecycle.heartbeat(
                job_id, worker_id, self.clock() + timedelta(seconds=self.lease_seconds)
            )

    async def set_phase(self, job_id: int, worker_id: str, phase: DownloadPhase) -> bool:
        async with self.database.transaction() as repositories:
            return await repositories.download_lifecycle.set_phase(job_id, worker_id, phase)

    async def begin_delivery(self, job_id: int, worker_id: str) -> bool:
        async with self.database.transaction() as repositories:
            return await repositories.download_lifecycle.begin_delivery(
                job_id, worker_id, self.clock()
            )

    async def succeed(
        self,
        job_id: int,
        worker_id: str,
        *,
        message_id: int | None = None,
        file_id: str | None = None,
    ) -> bool:
        async with self.database.transaction() as repositories:
            return await repositories.download_lifecycle.succeed(
                job_id=job_id,
                worker_id=worker_id,
                message_id=message_id,
                file_id=file_id,
                now=self.clock(),
            )

    async def fail(
        self, job_id: int, worker_id: str, code: DownloadFailureCode, message: str | None = None
    ) -> bool:
        async with self.database.transaction() as repositories:
            return await repositories.download_lifecycle.fail(
                job_id=job_id,
                worker_id=worker_id,
                now=self.clock(),
                error_code=code.value,
                error_message=message,
            )

    async def schedule_retry(
        self,
        job_id: int,
        worker_id: str,
        code: DownloadFailureCode,
        *,
        retry_after: float = 0.0,
        message: str | None = None,
    ) -> DownloadJobStatus | None:
        now = self.clock()
        async with self.database.transaction() as repositories:
            job = await repositories.download_lifecycle.get_job(job_id)
            if job is None:
                return None
            base = min(300.0, 2 ** max(job.attempt - 1, 0))
            jitter = base * 0.25 * self._random()
            retry_at = now + timedelta(seconds=max(base + jitter, retry_after))
            return await repositories.download_lifecycle.schedule_retry(
                job_id=job_id,
                worker_id=worker_id,
                now=now,
                retry_at=retry_at,
                error_code=code.value,
                error_message=message,
            )

    async def cancel(self, job_id: int) -> DownloadJobStatus | None:
        async with self.database.transaction() as repositories:
            return await repositories.download_lifecycle.cancel(job_id, self.clock())

    async def recover(self) -> int:
        now = self.clock()
        async with self.database.transaction() as repositories:
            recovered = await repositories.download_lifecycle.recover_expired(
                now=now, jitter_retry_at=now + timedelta(seconds=1 + self._random())
            )
            await repositories.provider_resolution.abandon_expired_jobs(now)
            await repositories.download_lifecycle.requeue_due(now)
            return recovered

    async def reconcile_telegram_delivery(
        self, telegram_request_id: int, status: str, error_code: str | None = None
    ) -> bool:
        async with self.database.transaction() as repositories:
            return await repositories.download_lifecycle.reconcile_telegram(
                telegram_request_id, status, error_code, self.clock()
            )


class DownloadWorkspaceManager:
    """Conservative per-job workspace ownership and orphan cleanup."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, job_id: int) -> Path:
        path = (self.root / str(job_id)).resolve()
        if self.root not in path.parents:
            raise ValueError("invalid job workspace")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cleanup(self, job_id: int) -> None:
        path = (self.root / str(job_id)).resolve()
        if self.root in path.parents and path.name == str(job_id) and path.exists():
            shutil.rmtree(path)

    def cleanup_orphans(self, active_job_ids: set[int]) -> int:
        removed = 0
        for child in self.root.iterdir():
            if child.is_dir() and child.name.isdigit() and int(child.name) not in active_job_ids:
                shutil.rmtree(child)
                removed += 1
        return removed


def classify_failure(exc: BaseException) -> DownloadFailureCode:
    """Conservative typed classification; unknown failures are terminal internal errors."""
    name = type(exc).__name__.lower()
    if isinstance(exc, (TimeoutError, ConnectionError)) or "timeout" in name or "network" in name:
        return DownloadFailureCode.NETWORK
    if "rate" in name or "flood" in name:
        return DownloadFailureCode.PROVIDER_RATE_LIMITED
    if "auth" in name or "credential" in name:
        return DownloadFailureCode.PROVIDER_AUTH
    if "notfound" in name or "unavailable" in name:
        return DownloadFailureCode.MEDIA_NOT_FOUND
    return DownloadFailureCode.INTERNAL


def is_retryable(code: DownloadFailureCode) -> bool:
    return code in {
        DownloadFailureCode.PROVIDER_TEMPORARY,
        DownloadFailureCode.PROVIDER_RATE_LIMITED,
        DownloadFailureCode.NETWORK,
        DownloadFailureCode.DELIVERY_TEMPORARY,
        DownloadFailureCode.PROVIDER_AUTH,
        DownloadFailureCode.WORKER_LOST,
    }
