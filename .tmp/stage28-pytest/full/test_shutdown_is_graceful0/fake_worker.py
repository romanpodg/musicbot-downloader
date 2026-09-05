
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
    if mode == "malformed" and method == "get_metadata":
        sys.stdout.write("{not-json}\n")
        sys.stdout.flush()
        continue
    if mode == "hang" and method in {"get_metadata", "check_source", "download_native"}:
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
    elif method == "list_searchable_providers":
        response = {
            "id": request_id,
            "ok": True,
            "result": ["bandcamp", "youtube_music"],
        }
    elif method == "search_tracks":
        response = {
            "id": request_id,
            "ok": True,
            "result": [{
                "provider": request["params"]["provider"],
                "provider_track_id": "candidate",
                "url": "https://artist.bandcamp.com/track/candidate",
            }],
        }
    elif method == "check_source":
        result = {
            "status": "AVAILABLE",
            "native": {"codec": "mp3", "container": "mp3", "bitrate_kbps": 128},
            "access_token": "must-not-be-used",
        }
        response = {
            "id": request_id,
            "ok": True,
            "result": [] if mode == "malformed_source" else result,
        }
    elif method == "tidal_device_authorization_start":
        response = {
            "id": request_id,
            "ok": True,
            "result": {
                "status": "started",
                "flow_id": "a" * 16,
                "verification_url": "https://login.tidal.com/device",
                "expires_in": 300,
                "interval": 1,
            },
        }
    elif method == "deezer_arl_authorize":
        assert request["params"]["arl"] == "stage133-ipc-test-secret"
        response = {
            "id": request_id,
            "ok": True,
            "result": {"status": "persisted"},
        }
    elif method == "reconcile_provider_lifecycle":
        response = {
            "id": request_id,
            "ok": True,
            "result": {"status": "reconciled", "cleaned_temporary_artifacts": 0},
        }
    elif method == "reset_provider_authentication":
        response = {
            "id": request_id,
            "ok": True,
            "result": {"status": "disconnected"},
        }
    elif method == "tidal_device_authorization_poll":
        response = {
            "id": request_id,
            "ok": True,
            "result": {"status": "pending", "retry_after": 1},
        }
    elif method == "tidal_device_authorization_cancel":
        response = {
            "id": request_id,
            "ok": True,
            "result": {"status": "cancelled"},
        }
    elif method == "download_native":
        response = {
            "id": request_id,
            "ok": True,
            "result": {"status": "AVAILABLE", "file_path": "unused"},
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
