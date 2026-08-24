"""Authorized, ephemeral provider-level health snapshots."""

from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import Protocol

from app.core.enums import (
    MusicProviderName,
    ProviderHealthErrorCode,
    ProviderHealthStatus,
)
from app.core.models import ProviderHealthEntry, ProviderHealthSnapshot
from app.providers.onthespot.capabilities import ONTHESPOT_CAPABILITIES, PROVIDER_ORDER
from app.services.authorization import (
    AdminAccessContext,
    AdminPermission,
    TelegramAuthorizationService,
)
from app.storage.models.base import utc_now

logger = logging.getLogger(__name__)

PROVIDER_HEALTH_TIMEOUT_SECONDS = 15.0


class ProviderHealthProbe(Protocol):
    async def refresh_provider_health_state(self) -> None: ...

    async def check_provider_health(self, provider: MusicProviderName) -> ProviderHealthEntry: ...


class ProviderHealthService:
    """Authorize callers, normalize failures, and coalesce only an active sweep."""

    def __init__(
        self,
        probe: ProviderHealthProbe,
        authorization: TelegramAuthorizationService | None,
    ) -> None:
        self._probe = probe
        self._authorization = authorization
        self._in_flight_lock = asyncio.Lock()
        self._in_flight: asyncio.Task[ProviderHealthSnapshot] | None = None

    @classmethod
    def for_local_operator(cls, probe: ProviderHealthProbe) -> ProviderHealthService:
        """Construct the explicit local-CLI variant; Telegram never uses this path."""

        return cls(probe, None)

    async def authorize(self, actor_user_id: int) -> AdminAccessContext:
        if self._authorization is None:
            raise RuntimeError("local provider-health service has no Telegram authorization")
        return await self._authorization.require_permission(
            actor_user_id, AdminPermission.PROVIDER_HEALTH_VIEW
        )

    async def check_all(self, actor_user_id: int) -> ProviderHealthSnapshot:
        await self.authorize(actor_user_id)
        return await self._coalesced_snapshot()

    async def check_all_local(self) -> ProviderHealthSnapshot:
        if self._authorization is not None:
            raise RuntimeError("Telegram provider-health service requires an actor")
        return await self._coalesced_snapshot()

    async def _coalesced_snapshot(self) -> ProviderHealthSnapshot:
        async with self._in_flight_lock:
            task = self._in_flight
            if task is None or task.done():
                task = asyncio.create_task(self._collect(), name="provider-health-sweep")
                self._in_flight = task
                task.add_done_callback(self._release_completed_sweep)
        return await asyncio.shield(task)

    def _release_completed_sweep(self, task: asyncio.Task[ProviderHealthSnapshot]) -> None:
        if self._in_flight is task:
            self._in_flight = None

    async def _collect(self) -> ProviderHealthSnapshot:
        started = monotonic()
        entries: list[ProviderHealthEntry] = []
        for provider in PROVIDER_ORDER:
            entry_started = monotonic()
            try:
                async with asyncio.timeout(PROVIDER_HEALTH_TIMEOUT_SECONDS):
                    entry = await self._probe.check_provider_health(provider)
                if entry.provider is not provider:
                    raise ValueError("provider-health response identity mismatch")
            except TimeoutError:
                entry = self._error_entry(provider, ProviderHealthErrorCode.HEALTH_CHECK_TIMEOUT)
            except Exception:
                entry = self._error_entry(provider, ProviderHealthErrorCode.UPSTREAM_ERROR)
            entries.append(entry)
            logger.info(
                "Provider health checked",
                extra={
                    "provider": provider.value,
                    "health_status": entry.status.value,
                    "error_code": entry.error_code.value if entry.error_code else None,
                    "duration_ms": round((monotonic() - entry_started) * 1000),
                },
            )
        return ProviderHealthSnapshot(
            checked_at=utc_now(),
            entries=tuple(entries),
            duration_ms=round((monotonic() - started) * 1000),
        )

    def _failed_snapshot(
        self, started: float, code: ProviderHealthErrorCode
    ) -> ProviderHealthSnapshot:
        return ProviderHealthSnapshot(
            checked_at=utc_now(),
            entries=tuple(self._error_entry(provider, code) for provider in PROVIDER_ORDER),
            duration_ms=round((monotonic() - started) * 1000),
        )

    @staticmethod
    def _error_entry(
        provider: MusicProviderName, code: ProviderHealthErrorCode
    ) -> ProviderHealthEntry:
        capabilities = ONTHESPOT_CAPABILITIES[provider]
        return ProviderHealthEntry(
            provider=provider,
            status=ProviderHealthStatus.ERROR,
            requires_authentication=bool(capabilities.requires_auth),
            download_supported=capabilities.download_supported,
            error_code=code,
        )
