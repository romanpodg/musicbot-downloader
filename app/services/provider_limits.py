"""Shared, provider-scoped execution pacing for Stage 27.

The limiter only delays local execution.  It never raises a provider failure,
updates account health, or changes Stage 25 candidate ordering.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic

from app.core.enums import MusicProviderName


@dataclass(frozen=True, slots=True)
class ProviderRateLimitSnapshot:
    """Bounded local pacing state; it intentionally says nothing about health."""

    provider: MusicProviderName
    active_operations: int
    waiting_operations: int

    @property
    def throttle(self) -> str:
        return "waiting" if self.waiting_operations else "ready"


class ProviderRateLimiter:
    def __init__(self, *, interval_seconds: float = 0.0, max_concurrent: int = 1) -> None:
        if interval_seconds < 0 or max_concurrent < 1:
            raise ValueError("invalid provider limiter configuration")
        self.interval_seconds = interval_seconds
        self.max_concurrent = max_concurrent
        self._semaphores: dict[MusicProviderName, asyncio.Semaphore] = {}
        self._locks: dict[MusicProviderName, asyncio.Lock] = {}
        self._next_allowed: dict[MusicProviderName, float] = {}
        self._active: dict[MusicProviderName, int] = {}
        self._waiting: dict[MusicProviderName, int] = {}

    @property
    def active(self) -> dict[MusicProviderName, int]:
        return dict(self._active)

    def snapshot(
        self, providers: tuple[MusicProviderName, ...] = ()
    ) -> tuple[ProviderRateLimitSnapshot, ...]:
        """Expose no provider data beyond local operation counts and pacing state."""

        observed = set(providers) | set(self._active) | set(self._waiting) | set(self._next_allowed)
        return tuple(
            ProviderRateLimitSnapshot(
                provider=provider,
                active_operations=self._active.get(provider, 0),
                waiting_operations=self._waiting.get(provider, 0),
            )
            for provider in sorted(observed, key=lambda item: item.value)
        )

    @asynccontextmanager
    async def operation(self, provider: MusicProviderName) -> AsyncIterator[None]:
        semaphore = self._semaphores.setdefault(provider, asyncio.Semaphore(self.max_concurrent))
        lock = self._locks.setdefault(provider, asyncio.Lock())
        self._waiting[provider] = self._waiting.get(provider, 0) + 1
        acquired = False
        waiting = True
        try:
            await semaphore.acquire()
            acquired = True
            async with lock:
                delay = max(0.0, self._next_allowed.get(provider, 0.0) - monotonic())
                if delay:
                    await asyncio.sleep(delay)
                self._next_allowed[provider] = monotonic() + self.interval_seconds
                self._waiting[provider] = max(0, self._waiting.get(provider, 1) - 1)
                waiting = False
                self._active[provider] = self._active.get(provider, 0) + 1
            try:
                yield
            finally:
                self._active[provider] = max(0, self._active.get(provider, 1) - 1)
        finally:
            if waiting and self._waiting.get(provider, 0):
                self._waiting[provider] -= 1
            if acquired:
                semaphore.release()


__all__ = ["ProviderRateLimiter", "ProviderRateLimitSnapshot"]
