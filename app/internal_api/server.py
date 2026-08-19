"""Embedded non-blocking uvicorn lifecycle."""

from __future__ import annotations

import asyncio

import uvicorn
from fastapi import FastAPI


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
        self._server = uvicorn.Server(config)
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
