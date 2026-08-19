"""Embedded non-blocking uvicorn lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn
from fastapi import FastAPI


class _EmbeddedUvicornServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        """Leave process signals under the application-level supervisor."""

        yield


class InternalApiServer:
    def __init__(self, app: FastAPI, *, host: str, port: int) -> None:
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_config=None,
            access_log=False,
            lifespan="on",
        )
        self._server = _EmbeddedUvicornServer(config)
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._server.serve(), name="internal-api-server")
        for _ in range(500):
            if self._server.started:
                return
            if self._task.done():
                await self._task
                self._task = None
                raise RuntimeError("Internal API server terminated during startup")
            await asyncio.sleep(0.01)
        await self.stop()
        raise RuntimeError("Internal API server did not start")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._server.should_exit = True
        try:
            await asyncio.wait_for(self._task, timeout=10)
        finally:
            self._task = None

    async def wait_terminated(self) -> None:
        """Wait for unexpected listener termination so the process supervisor can fail."""

        task = self._task
        if task is None:
            raise RuntimeError("Internal API server is not running")
        await asyncio.shield(task)
