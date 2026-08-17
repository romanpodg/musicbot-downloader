from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from app.core.exceptions import ProviderUnavailable
from app.providers.onthespot.process import (
    OnTheSpotProcessClient,
    close_shared_process_client,
)
from app.providers.onthespot.provider import OnTheSpotProvider

_FAKE_WORKER = r"""
import json
import os
import sys
import time

mode = os.environ.get("FAKE_WORKER_MODE", "normal")
sentinel = os.environ.get("FAKE_WORKER_SENTINEL")
for line in sys.stdin.buffer:
    request = json.loads(line)
    request_id = request["id"]
    method = request["method"]
    if mode == "crash" and method == "get_metadata":
        os._exit(7)
    if mode == "hang" and method == "get_metadata":
        time.sleep(10)
    if mode == "init_fail" and method == "initialize":
        response = {
            "id": request_id,
            "ok": False,
            "error": {"code": "provider_unavailable", "message": "unavailable"},
        }
    elif method == "initialize":
        response = {
            "id": request_id,
            "ok": True,
            "result": {"protocol": 1, "version": "fake"},
        }
    elif method == "get_metadata":
        response = {
            "id": request_id,
            "ok": True,
            "result": {
                "protobuf": os.environ.get("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"),
                "upstream_log_level": os.environ.get("LOG_LEVEL"),
                "app_log_level": os.environ.get("APP_LOG_LEVEL"),
            },
        }
    elif method == "shutdown":
        if sentinel:
            with open(sentinel, "w", encoding="utf-8") as handle:
                handle.write("closed")
        response = {"id": request_id, "ok": True, "result": {}}
    else:
        response = {
            "id": request_id,
            "ok": False,
            "error": {"code": "metadata_unavailable", "message": "unsupported"},
        }
    sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    if method == "shutdown":
        break
"""


def _client(
    tmp_path: Path, *, request_timeout: float = 2, **environment: str
) -> OnTheSpotProcessClient:
    worker = tmp_path / "fake_worker.py"
    worker.write_text(_FAKE_WORKER, encoding="utf-8")
    child_environment = dict(os.environ)
    child_environment.update(environment)
    return OnTheSpotProcessClient(
        command=(sys.executable, str(worker)),
        environment=child_environment,
        request_timeout=request_timeout,
    )


@pytest.mark.asyncio
async def test_child_environment_is_controlled_without_mutating_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_LOG_LEVEL", "INFO")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.delenv("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", raising=False)
    client = _client(tmp_path)
    try:
        availability = await client.availability()
        result = await client.get_metadata("https://example.invalid")
    finally:
        await client.close()

    assert availability.available is True
    assert availability.version == "fake"
    assert result["protobuf"] == "python"
    assert result["upstream_log_level"] == "20"
    assert result["app_log_level"] == "INFO"
    assert os.environ["LOG_LEVEL"] == "INFO"
    assert "PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION" not in os.environ


@pytest.mark.asyncio
async def test_initialization_failure_is_unavailable(tmp_path: Path) -> None:
    client = _client(tmp_path, FAKE_WORKER_MODE="init_fail")
    try:
        availability = await client.availability()
        assert availability.available is False
        with pytest.raises(ProviderUnavailable):
            await client.get_metadata("https://example.invalid")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_worker_crash_is_terminal_and_does_not_respawn(tmp_path: Path) -> None:
    client = _client(tmp_path, FAKE_WORKER_MODE="crash")
    assert (await client.availability()).available is True
    process_id = client.process_id
    with pytest.raises(ProviderUnavailable):
        await client.get_metadata("https://example.invalid")
    with pytest.raises(ProviderUnavailable):
        await client.get_metadata("https://example.invalid")
    assert client.process_id is None
    assert process_id is not None
    await client.close()


@pytest.mark.asyncio
async def test_request_timeout_terminates_worker(tmp_path: Path) -> None:
    client = _client(tmp_path, request_timeout=1, FAKE_WORKER_MODE="hang")
    assert (await client.availability()).available is True
    with pytest.raises(ProviderUnavailable):
        await client.get_metadata("https://example.invalid")
    assert client.process_id is None
    await client.close()


@pytest.mark.asyncio
async def test_shutdown_is_graceful(tmp_path: Path) -> None:
    sentinel = tmp_path / "closed.txt"
    client = _client(tmp_path, FAKE_WORKER_SENTINEL=str(sentinel))
    assert (await client.availability()).available is True
    await client.close()
    assert client.closed is True
    assert client.process_id is None
    assert sentinel.read_text(encoding="utf-8") == "closed"


@pytest.mark.asyncio
async def test_default_provider_instances_share_one_process_boundary() -> None:
    first = OnTheSpotProvider()
    second = OnTheSpotProvider()
    try:
        assert first._process_client is second._process_client
    finally:
        await close_shared_process_client()


def test_worker_sets_protobuf_mode_before_upstream_imports() -> None:
    worker_path = Path(__file__).parents[2] / "app" / "providers" / "onthespot" / "worker.py"
    source = worker_path.read_text(encoding="utf-8")
    environment_setup = source.index(
        'os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"'
    )
    first_dynamic_import = source.index("import importlib")
    assert environment_setup < first_dynamic_import


def test_main_process_provider_modules_have_no_upstream_imports() -> None:
    provider_root = Path(__file__).parents[2] / "app" / "providers" / "onthespot"
    for filename in ("__init__.py", "process.py", "provider.py"):
        source = (provider_root / filename).read_text(encoding="utf-8")
        assert "import onthespot" not in source
        assert "from onthespot" not in source
        assert "import librespot" not in source
