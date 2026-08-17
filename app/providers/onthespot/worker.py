"""Isolated OnTheSpot JSON Lines worker.

This module intentionally sets compatibility environment variables before any
third-party provider import. Protocol output is the original stdout stream;
upstream stdout and stderr are discarded so they cannot corrupt framing or
reach parent application logs.
"""

from __future__ import annotations

import os

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
os.environ["LOG_LEVEL"] = "20"

import importlib
import importlib.metadata
import json
import sys
from collections.abc import Mapping
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, TextIO

from app.providers.onthespot.ipc import (
    GET_METADATA_METHOD,
    INITIALIZE_METHOD,
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    SHUTDOWN_METHOD,
)

_AUTHENTICATED_SERVICES = frozenset(
    {"apple_music", "deezer", "qobuz", "soundcloud", "spotify", "tidal"}
)
_WIRE_METADATA_KEYS = frozenset(
    {
        "album_name",
        "artists",
        "explicit",
        "is_playable",
        "isrc",
        "item_id",
        "length",
        "release_date",
        "release_year",
        "title",
    }
)


class WorkerError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class OnTheSpotWorker:
    def __init__(self) -> None:
        self._initialized = False
        self._accounts: Any = None
        self._parse: Any = None
        self._registry: Any = None

    def initialize(self) -> dict[str, Any]:
        if self._initialized:
            return self._initialization_result()
        self._validate_config_location()
        try:
            with _silence_upstream():
                self._accounts = importlib.import_module("onthespot.accounts")
                self._parse = importlib.import_module("onthespot.parse_item")
                self._registry = importlib.import_module("onthespot.api.registry")
                self._accounts.AccountPoolLoader(gui=False).run()
            self._initialized = True
        except Exception as exc:
            raise WorkerError("provider_unavailable") from exc
        return self._initialization_result()

    def get_metadata(self, url: str) -> dict[str, Any]:
        if not self._initialized:
            self.initialize()
        try:
            with _silence_upstream():
                resolved = self._parse.UrlMatcher().match(url)
        except Exception as exc:
            raise WorkerError("metadata_unavailable") from exc
        if resolved is None:
            raise WorkerError("invalid_track_url")
        service, item_type, item_id = resolved
        if service == "__handled__" or item_type != "track" or not item_id:
            raise WorkerError("invalid_track_url")

        try:
            with _silence_upstream():
                token = self._accounts.get_account_token(service)
                if service in _AUTHENTICATED_SERVICES and token is None:
                    raise WorkerError("provider_authentication_error")
                metadata_function = self._registry.get_metadata_function(service, item_type)
                raw = metadata_function(token, item_id)
        except WorkerError:
            raise
        except (KeyError, IndexError) as exc:
            raise WorkerError("provider_authentication_error") from exc
        except Exception as exc:
            raise WorkerError("metadata_unavailable") from exc
        if not isinstance(raw, Mapping) or not raw:
            raise WorkerError("metadata_unavailable")

        return {
            "service": str(service),
            "item_type": str(item_type),
            "item_id": str(item_id),
            "metadata": {
                key: _wire_value(raw[key])
                for key in _WIRE_METADATA_KEYS
                if key in raw and _wire_value(raw[key]) is not None
            },
        }

    @staticmethod
    def _validate_config_location() -> None:
        configured = os.environ.get("ONTHESPOTDIR")
        if not configured:
            return
        config_dir = Path(configured).expanduser().resolve()
        repository_root = Path(__file__).resolve().parents[3]
        if config_dir == repository_root or repository_root in config_dir.parents:
            raise WorkerError("provider_unavailable")

    @staticmethod
    def _initialization_result() -> dict[str, Any]:
        try:
            version = importlib.metadata.version("onthespot")
        except importlib.metadata.PackageNotFoundError:
            version = None
        return {"protocol": PROTOCOL_VERSION, "version": version}


class _silence_upstream:
    def __init__(self) -> None:
        self._sink: TextIO | None = None
        self._stdout: Any = None
        self._stderr: Any = None

    def __enter__(self) -> None:
        self._sink = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        self._stdout = redirect_stdout(self._sink)
        self._stderr = redirect_stderr(self._sink)
        self._stdout.__enter__()
        self._stderr.__enter__()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._stderr.__exit__(exc_type, exc, traceback)
        self._stdout.__exit__(exc_type, exc, traceback)
        if self._sink is not None:
            self._sink.close()


def _wire_value(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _response(request_id: str | None, *, result: Any = None, error: str | None = None) -> bytes:
    if error is None:
        payload: dict[str, Any] = {"id": request_id, "ok": True, "result": result}
    else:
        payload = {
            "id": request_id,
            "ok": False,
            "error": {"code": error, "message": "OnTheSpot operation failed"},
        }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_MESSAGE_BYTES:
        return _response(request_id, error="metadata_unavailable")
    return encoded + b"\n"


def main() -> int:
    protocol_stdout = sys.stdout.buffer
    worker = OnTheSpotWorker()
    while True:
        line = sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1)
        if not line:
            return 0
        request_id: str | None = None
        if len(line) > MAX_MESSAGE_BYTES or not line.endswith(b"\n"):
            protocol_stdout.write(_response(None, error="provider_unavailable"))
            protocol_stdout.flush()
            return 2
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError
            raw_id = request.get("id")
            if not isinstance(raw_id, str) or not raw_id or len(raw_id) > 128:
                raise ValueError
            request_id = raw_id
            method = request.get("method")
            params = request.get("params")
            if not isinstance(params, dict):
                raise ValueError
            if method == INITIALIZE_METHOD:
                result = worker.initialize()
            elif method == GET_METADATA_METHOD:
                url = params.get("url")
                if not isinstance(url, str):
                    raise WorkerError("invalid_track_url")
                result = worker.get_metadata(url)
            elif method == SHUTDOWN_METHOD:
                protocol_stdout.write(_response(request_id, result={"stopped": True}))
                protocol_stdout.flush()
                return 0
            else:
                raise WorkerError("provider_unavailable")
            response = _response(request_id, result=result)
        except WorkerError as exc:
            response = _response(request_id, error=exc.code)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            response = _response(request_id, error="provider_unavailable")
        except Exception:
            response = _response(request_id, error="provider_unavailable")
        protocol_stdout.write(response)
        protocol_stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
