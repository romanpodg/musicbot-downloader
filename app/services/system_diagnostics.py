"""Bounded operational read models shared by CLI and admin Telegram views."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol

from app.core.enums import MusicProviderName
from app.core.models import QueueRuntimeSnapshot
from app.services.authorization import AdminPermission, TelegramAuthorizationService
from app.services.provider_health import ProviderHealthService
from app.services.provider_limits import ProviderRateLimiter
from app.storage import Database
from app.storage.models.base import utc_now


class QueueReader(Protocol):
    async def snapshot(self) -> QueueRuntimeSnapshot: ...


@dataclass(frozen=True, slots=True)
class StorageDiagnostic:
    used_bytes: int
    free_bytes: int
    reserve_bytes: int
    maximum_bytes: int
    pressure: str


@dataclass(frozen=True, slots=True)
class ProviderDiagnostic:
    provider: MusicProviderName
    health: str
    active_operations: int
    throttle: str


@dataclass(frozen=True, slots=True)
class SystemDiagnostic:
    queues: QueueRuntimeSnapshot
    storage: StorageDiagnostic
    recent_failures: int
    expired_claims: int
    providers: tuple[ProviderDiagnostic, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class JobDiagnostic:
    job_id: int
    status: str
    attempt_count: int
    last_error_code: str | None
    started_at: Any
    finished_at: Any
    provider_attempts: tuple[dict[str, Any], ...]


class SystemDiagnosticsService:
    def __init__(
        self,
        database: Database,
        queues: QueueReader,
        *,
        temp_dir: Path,
        temp_reserve_bytes: int,
        temp_max_bytes: int,
        stuck_threshold_seconds: float = 1800.0,
        per_user_active_limit: int | None = None,
        provider_health: ProviderHealthService | None = None,
        provider_limiter: ProviderRateLimiter | None = None,
        authorization: TelegramAuthorizationService | None = None,
    ) -> None:
        if min(temp_reserve_bytes, temp_max_bytes) < 0 or stuck_threshold_seconds <= 0:
            raise ValueError("invalid diagnostic limits")
        self._database = database
        self._queues = queues
        self._temp_dir = temp_dir.expanduser().resolve()
        self._reserve = temp_reserve_bytes
        self._maximum = temp_max_bytes
        self._stuck = stuck_threshold_seconds
        self._per_user_active_limit = per_user_active_limit
        self._provider_health = provider_health
        self._provider_limiter = provider_limiter
        self._authorization = authorization

    async def system(self, actor_user_id: int | None = None) -> SystemDiagnostic:
        await self._authorize(actor_user_id)
        queues = await self._queues.snapshot()
        now = utc_now()
        cutoff = now - timedelta(hours=1)
        async with self._database.transaction() as repositories:
            recent = await repositories.download_jobs.count_terminal_failures(cutoff)
            recent += await repositories.upload_jobs.count_terminal_failures(cutoff)
            expired = await repositories.download_jobs.count_expired_leases(now)
            expired += await repositories.upload_jobs.count_expired_leases(now)
            expired += await repositories.download_jobs.count_stuck_jobs(
                now - timedelta(seconds=self._stuck)
            )
            expired += await repositories.upload_jobs.count_stuck_jobs(
                now - timedelta(seconds=self._stuck)
            )
            user_cap_blocked = (
                await repositories.download_jobs.count_queued_blocked_by_user_limit(
                    self._per_user_active_limit
                )
                if self._per_user_active_limit is not None
                else 0
            )
        usage = shutil.disk_usage(self._temp_dir)
        used = _directory_size(self._temp_dir)
        pressure = "normal"
        if usage.free < self._reserve or used >= self._maximum:
            pressure = "blocked"
        elif usage.free < self._reserve * 2 or used > self._maximum * 0.9:
            pressure = "warning"
        providers = await self._provider_diagnostics(actor_user_id)
        reasons: list[str] = []
        if queues.download_jobs.queued or queues.upload_jobs.queued:
            reasons.append("normal queued work")
        if (
            queues.download_jobs.queued
            and queues.download.actual_workers >= queues.download.desired_workers
        ):
            reasons.append("global download capacity saturation")
        if user_cap_blocked:
            reasons.append("per-user download concurrency cap")
        if any(provider.throttle == "waiting" for provider in providers):
            reasons.append("provider local throttling")
        if pressure != "normal":
            reasons.append("storage pressure")
        if expired:
            reasons.append("expired or stuck work")
        return SystemDiagnostic(
            queues=queues,
            storage=StorageDiagnostic(used, usage.free, self._reserve, self._maximum, pressure),
            recent_failures=recent,
            expired_claims=expired,
            providers=providers,
            reasons=tuple(reasons),
        )

    async def job(self, job_id: int, actor_user_id: int | None = None) -> JobDiagnostic | None:
        await self._authorize(actor_user_id)
        if job_id <= 0:
            return None
        async with self._database.transaction() as repositories:
            job = await repositories.download_jobs.get(job_id)
            if job is None:
                return None
            attempts: tuple[dict[str, Any], ...] = ()
            lifecycle = await repositories.download_lifecycle.latest_request_for_track(job.track_id)
            if lifecycle is not None:
                row = await repositories.download_lifecycle.get_job_for_request(lifecycle.id)
                if row is not None:
                    attempts = tuple(
                        {
                            "status": item.status,
                            "failure_code": item.failure_code,
                            "started_at": item.started_at,
                            "finished_at": item.finished_at,
                        }
                        for item in await repositories.provider_resolution.list_attempts(row.id)
                    )
            return JobDiagnostic(
                job.id,
                job.status.value,
                job.attempt_count,
                job.last_error_code,
                job.started_at,
                job.finished_at,
                attempts[:50],
            )

    async def _authorize(self, actor_user_id: int | None) -> None:
        if self._authorization is not None:
            if actor_user_id is None:
                raise PermissionError("administrator authorization required")
            await self._authorization.require_permission(
                actor_user_id, AdminPermission.ADMIN_PANEL_VIEW
            )

    async def _provider_diagnostics(
        self, actor_user_id: int | None
    ) -> tuple[ProviderDiagnostic, ...]:
        health_by_provider: dict[MusicProviderName, str] = {}
        if self._provider_health is not None:
            if actor_user_id is None:
                return ()
            snapshot = await self._provider_health.check_all(actor_user_id)
            health_by_provider = {
                entry.provider: entry.status.value.lower() for entry in snapshot.entries
            }
        limiter = self._provider_limiter
        limiter_by_provider = (
            {item.provider: item for item in limiter.snapshot(tuple(health_by_provider))}
            if limiter is not None
            else {}
        )
        providers = sorted(
            set(health_by_provider) | set(limiter_by_provider), key=lambda item: item.value
        )
        diagnostics: list[ProviderDiagnostic] = []
        for provider in providers:
            limit = limiter_by_provider.get(provider)
            diagnostics.append(
                ProviderDiagnostic(
                    provider=provider,
                    health=health_by_provider.get(provider, "unknown"),
                    active_operations=limit.active_operations if limit is not None else 0,
                    throttle=limit.throttle if limit is not None else "ready",
                )
            )
        return tuple(diagnostics)


def _directory_size(root: Path) -> int:
    total = 0
    if not root.exists():
        return 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total


__all__ = [
    "JobDiagnostic",
    "ProviderDiagnostic",
    "StorageDiagnostic",
    "SystemDiagnostic",
    "SystemDiagnosticsService",
]
