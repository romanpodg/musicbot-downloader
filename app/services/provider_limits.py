"""Shared, provider-scoped execution pacing for Stage 27.

The limiter only delays local execution.  It never raises a provider failure,
updates account health, or changes Stage 25 candidate ordering.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import monotonic

from app.core.enums import MusicProviderName


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

    @property
    def active(self) -> dict[MusicProviderName, int]:
        return dict(self._active)

    @asynccontextmanager
    async def operation(self, provider: MusicProviderName) -> AsyncIterator[None]:
        semaphore = self._semaphores.setdefault(provider, asyncio.Semaphore(self.max_concurrent))
        lock = self._locks.setdefault(provider, asyncio.Lock())
        await semaphore.acquire()
        try:
            async with lock:
                delay = max(0.0, self._next_allowed.get(provider, 0.0) - monotonic())
                if delay:
                    await asyncio.sleep(delay)
                self._next_allowed[provider] = monotonic() + self.interval_seconds
                self._active[provider] = self._active.get(provider, 0) + 1
            try:
                yield
            finally:
                self._active[provider] = max(0, self._active.get(provider, 1) - 1)
        finally:
            semaphore.release()


__all__ = ["ProviderRateLimiter"]
