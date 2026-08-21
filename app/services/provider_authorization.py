"""In-process provider-neutral authorization-flow coordination foundation."""

from __future__ import annotations

import asyncio
from typing import Protocol

from app.core.enums import MusicProviderName
from app.core.provider_accounts import (
    ProviderAccountErrorCode,
    ProviderAuthorizationMethod,
    ProviderAuthorizationOutcome,
    ProviderAuthorizationOutcomeStatus,
    ProviderAuthorizationRequest,
    ProviderCompoundCredentialInput,
    ProviderSecretInput,
)


class ProviderAuthorizationDriver(Protocol):
    """Generic driver boundary; implementations remain provider-owned."""

    async def authorize(
        self, request: ProviderAuthorizationRequest
    ) -> ProviderAuthorizationOutcome: ...


class BrowserDeviceAuthorizationDriver(ProviderAuthorizationDriver, Protocol):
    """Boundary for a future browser/device-link flow."""


class SensitiveSecretAuthorizationDriver(Protocol):
    """Boundary for a future one-secret flow such as an ARL."""

    async def authorize_secret(
        self, credential: ProviderSecretInput
    ) -> ProviderAuthorizationOutcome: ...


class CompoundCredentialAuthorizationDriver(Protocol):
    """Boundary for future playback plus API credentials."""

    async def authorize_credentials(
        self, credentials: ProviderCompoundCredentialInput
    ) -> ProviderAuthorizationOutcome: ...


class ProviderAuthorizationCoordinator:
    """Allow at most one ephemeral authorization flow per provider per process."""

    def __init__(
        self,
        drivers: dict[
            tuple[MusicProviderName, ProviderAuthorizationMethod], ProviderAuthorizationDriver
        ]
        | None = None,
    ) -> None:
        self._drivers = dict(drivers or {})
        self._active: dict[MusicProviderName, asyncio.Task[ProviderAuthorizationOutcome]] = {}
        self._lock = asyncio.Lock()

    def available_methods(
        self, provider: MusicProviderName
    ) -> tuple[ProviderAuthorizationMethod, ...]:
        return tuple(method for candidate, method in self._drivers if candidate is provider)

    async def is_active(self, provider: MusicProviderName) -> bool:
        async with self._lock:
            task = self._active.get(provider)
            return task is not None and not task.done()

    async def authorize(
        self, request: ProviderAuthorizationRequest
    ) -> ProviderAuthorizationOutcome:
        driver = self._drivers.get((request.provider, request.method))
        if driver is None:
            return ProviderAuthorizationOutcome(
                request.provider,
                ProviderAuthorizationOutcomeStatus.UNSUPPORTED,
                ProviderAccountErrorCode.AUTHORIZATION_UNSUPPORTED,
            )
        async with self._lock:
            existing = self._active.get(request.provider)
            if existing is not None and not existing.done():
                return ProviderAuthorizationOutcome(
                    request.provider, ProviderAuthorizationOutcomeStatus.ALREADY_ACTIVE
                )
            task = asyncio.create_task(
                self._run_driver(driver, request),
                name=f"provider-authorization-{request.provider.value}",
            )
            self._active[request.provider] = task
            asyncio.create_task(
                self._release_when_done(request.provider, task),
                name=f"provider-authorization-release-{request.provider.value}",
            )
        return await asyncio.shield(task)

    async def cancel(self, provider: MusicProviderName) -> ProviderAuthorizationOutcome:
        async with self._lock:
            task = self._active.get(provider)
            if task is None or task.done():
                return ProviderAuthorizationOutcome(
                    provider,
                    ProviderAuthorizationOutcomeStatus.UNSUPPORTED,
                    ProviderAccountErrorCode.AUTHORIZATION_UNSUPPORTED,
                )
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return ProviderAuthorizationOutcome(provider, ProviderAuthorizationOutcomeStatus.CANCELLED)

    async def close(self) -> None:
        """Cancel any ephemeral flows during normal application shutdown."""

        async with self._lock:
            tasks = tuple(task for task in self._active.values() if not task.done())
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        async with self._lock:
            self._active.clear()

    async def _run_driver(
        self,
        driver: ProviderAuthorizationDriver,
        request: ProviderAuthorizationRequest,
    ) -> ProviderAuthorizationOutcome:
        try:
            outcome = await driver.authorize(request)
        except asyncio.CancelledError:
            raise
        except Exception:
            return ProviderAuthorizationOutcome(
                request.provider,
                ProviderAuthorizationOutcomeStatus.FAILED,
                ProviderAccountErrorCode.AUTHORIZATION_FAILED,
            )
        if outcome.provider is not request.provider:
            return ProviderAuthorizationOutcome(
                request.provider,
                ProviderAuthorizationOutcomeStatus.FAILED,
                ProviderAccountErrorCode.AUTHORIZATION_FAILED,
            )
        return outcome

    async def _release_when_done(
        self, provider: MusicProviderName, task: asyncio.Task[ProviderAuthorizationOutcome]
    ) -> None:
        try:
            await asyncio.shield(task)
        except BaseException:
            pass
        async with self._lock:
            if self._active.get(provider) is task:
                self._active.pop(provider, None)
