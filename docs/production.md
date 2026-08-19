# Production deployment (Stage 12.1)

> **Single-instance constraint:** The current SQLite deployment supports one downloader
> application instance. Do not run replicas or point multiple application containers at the same
> database volume.

Stage 12.1 packages the existing `python -m app.main` runtime as one service. The process owns
Telegram long polling, the in-process worker managers, the optional embedded Internal API, and one
lazy serialized OnTheSpot child. It does not add a second downloader process, Redis, PostgreSQL, or
persistent local media storage.

## Requirements and image

- Docker Engine with Compose v2, or an equivalent container runtime;
- outbound HTTPS access for Telegram and configured providers;
- local persistent block/filesystem storage for SQLite; avoid unreliable network filesystems;
- a private Telegram cache chat and valid runtime secrets.

The multi-stage `Dockerfile` uses Python 3.12. The builder uses `uv sync --locked --no-dev --extra
onthespot`; a stale `uv.lock` therefore fails the build, the dev extra is absent, and OnTheSpot stays
pinned to upstream commit `8ed6cf33ef772e6569d5014237e0fb4ce8b9e45d`. The runtime stage contains
FFmpeg/ffprobe and the narrow shared libraries needed by the locked headless PyQt/media stack. It
runs directly as UID/GID `10001:10001` (`musicbot`) with an exec-form command. No compiler, Git,
`uv`, test suite, or development tools are copied into the runtime stage.

Build a versioned local image:

```bash
docker compose build
# or
docker build -t musicbot-downloader:stage12.1 .
```

## Filesystem and permissions

| Container path | Lifetime | Contents |
|---|---|---|
| `/data` | persistent named volume | SQLite DB/WAL/SHM, explicit OnTheSpot state, XDG configuration |
| `/data/onthespot` | persistent | OnTheSpot account/config state selected by `ONTHESPOTDIR` |
| `/tmp/musicbot` | ephemeral container layer | per-job media, tool/cache/home scratch |
| `/app` | immutable application files | Python package and Alembic migrations |

Completed audio is uploaded to the Telegram cache chat and released from `TEMP_DIR`; it is never
stored in `/data`. Container replacement preserves users, Tracks, queues/history, Telegram file
IDs, deep links, and desired worker counts in SQLite. It also preserves provider sessions under
`/data/onthespot`. Back up that provider directory separately because its files can contain account
secrets. Use restrictive permissions and never log or bake it into the image.

The image pre-creates writable paths for UID/GID 10001. A new named volume inherits that ownership.
For host bind mounts on Linux, prepare ownership explicitly:

```bash
sudo install -d -o 10001 -g 10001 -m 0750 ./data ./runtime-temp
```

Then bind `./data:/data` and `./runtime-temp:/tmp/musicbot`. Do not use mode `0777`. A host-managed
scratch bind also permits `read_only: true`; add writable `/data` and `/tmp/musicbot` mounts. A
tmpfs for `/tmp/musicbot` is optional, but only when its memory/size limit safely accommodates
large lossless jobs. The default Compose file leaves temporary media in the disposable container
layer rather than a persistent named volume.

`PYTHONDONTWRITEBYTECODE=1` prevents source-tree writes. `HOME`, `XDG_CONFIG_HOME`,
`XDG_CACHE_HOME`, `TMPDIR`, and `ONTHESPOTDIR` are explicit. `QT_QPA_PLATFORM=offscreen` avoids a
display server. The parent keeps `APP_LOG_LEVEL`; only the isolated child receives numeric
`LOG_LEVEL=20` and `PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python` before upstream imports.

## Configuration

Copy the safe template and protect the resulting secret-bearing file:

```bash
cp .env.example .env
chmod 600 .env
```

| Variable | Purpose / production value |
|---|---|
| `BOT_TOKEN` | Secret downloader-bot token; required at runtime |
| `TELEGRAM_CACHE_CHAT_ID` | Private durable media cache chat/channel |
| `OWNER_ID` | Optional positive Telegram user ID; the value is never logged |
| `DATABASE_URL` | Compose forces `sqlite+aiosqlite:////data/musicbot.db` |
| `TEMP_DIR` | Compose forces ephemeral `/tmp/musicbot` |
| `TEMP_DISK_MIN_FREE_BYTES` | Acquisition safety floor; default `268435456` (256 MiB) |
| `FFMPEG_BINARY`, `FFPROBE_BINARY` | Optional explicit executable paths; PATH by default |
| `DOWNLOAD_TIMEOUT_SECONDS`, `TRANSCODE_TIMEOUT_SECONDS` | Positive operation timeouts |
| `DOWNLOAD_WORKERS_DEFAULT`, `DOWNLOAD_WORKERS_MAX` | Initial and maximum download workers |
| `UPLOAD_WORKERS_DEFAULT`, `UPLOAD_WORKERS_MAX` | Initial and maximum upload workers |
| `QUEUE_MAX_SIZE` | Persistent queue admission bound |
| `INTERNAL_API_ENABLED` | `false` by default |
| `INTERNAL_API_HOST`, `INTERNAL_API_PORT` | Secure default `127.0.0.1:8081` |
| `INTERNAL_API_TOKEN` | Runtime secret, at least 32 trimmed characters when enabled |
| `APP_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`; default `INFO` |
| `ONTHESPOTDIR` | Compose forces controlled persistent `/data/onthespot` |

Inject secrets through the deployment environment, Docker secrets adapted into environment
variables, or a protected systemd `EnvironmentFile`. Do not place them in Docker build arguments,
the Dockerfile, source control, image labels, or support logs. `.dockerignore` excludes `.env*`,
local databases, provider config, temporary media, caches, Git metadata, IDE state, tests, and
coverage artifacts; `.env.example` is the only safe exception.

Prepare `otsconfig.json` using OnTheSpot's supported trusted-host workflow, then copy it into
`/data/onthespot` with owner 10001 and mode 0600. The production service does not run a GUI or
desktop environment and does not write to `/root`.

## Initialization and operation

Normal startup never migrates the database. Run these phases explicitly:

```bash
docker compose build
docker compose run --rm --no-deps musicbot alembic upgrade head
docker compose run --rm --no-deps musicbot python -m app.main --check
docker compose up -d
docker compose logs -f musicbot
docker compose stop musicbot
```

`--check` validates static Telegram/Internal API configuration, locale catalogs, TEMP_DIR
creation/write/delete, database connectivity, and exact Alembic revision. It performs no Telegram
`getMe`, provider request/health sweep, download, or HTTP socket bind. Missing FFmpeg/ffprobe is a
degraded warning rather than fatal so Telegram cache hits remain serviceable; both tools are
guaranteed by the production image. An inaccessible database, wrong schema, invalid required
configuration, or unwritable TEMP_DIR fails startup before workers begin.

Exact runtime startup order is: load settings; configure stdout/stderr logging; validate local
filesystem/media prerequisites; create the database engine; verify the Alembic head; reconcile
the configured OWNER; create Telegram and obtain bot identity; compose provider/runtime services;
initialize existing queue/SingleFlight state and start Download/Upload workers; start delivery and
album workers; start the optional Internal API; create Telegram polling; mark ready.

SIGTERM/SIGINT cancels polling and runs bounded worker shutdown, stops uvicorn, closes the
OnTheSpot child (terminate then kill after its existing bound if needed), closes Telegram, and
disposes the DB engine. Individual Download/Upload worker exceptions remain contained by their
existing pools. Unexpected termination of Telegram polling or the enabled Internal API is fatal;
the process shuts down all resources and exits nonzero so the service manager can restart it.
Deterministic config/schema errors will keep failing under `restart: unless-stopped`; run `--check`
and inspect logs before startup.

## Health and readiness

When the Internal API is enabled:

- `GET /internal/v1/health` is unauthenticated liveness and returns only `{"status":"ok"}`;
- `GET /internal/v1/ready` is unauthenticated readiness, returning only
  `{"status":"ready"}` or HTTP 503 `{"status":"not_ready"}`.

Readiness means schema validation, composition, worker startup, listener startup (when enabled),
and polling task creation completed. It does **not** mean any provider is authenticated or able to
download; Provider Health is separate. Transient provider failure and queue depth never affect
liveness/readiness.

The image intentionally has no Docker `HEALTHCHECK`: the Internal API is optional, and a separate
`--check` process could not truthfully prove that the real bot process/event loop is alive. Docker
process state/restart policy supplies liveness when the API is disabled. Use the HTTP endpoints
from a private-network orchestrator when enabled and external Telegram/runtime monitoring where
needed.

The secure application default binds the API to loopback. The default Compose file publishes no
ports. For another container on a private Docker network, deliberately set
`INTERNAL_API_HOST=0.0.0.0` and publish no host port. For host-loopback access only, additionally
publish `127.0.0.1:8081:8081`. Never use a default `0.0.0.0:8081:8081` host publication. The API
uses bearer authentication but no TLS; transport must remain on a trusted private network or be
terminated by operator-managed private TLS. CORS remains disabled.

## Logs and disk protection

Logs are one-line UTC text on stdout/stderr. Repeated setup replaces handlers instead of
duplicating them. The shared formatter redacts configured tokens, Telegram Bot API token URLs,
Bearer credentials, provider access/refresh-token-like values, cookies, and ARL-like secrets.
Startup logs include only backend type, private bind, worker limits, TEMP_DIR, and whether an owner
is configured. Relevant existing job/request identifiers are appended as correlation fields.

Before every fresh Stage 6 acquisition, free space on the TEMP_DIR filesystem must be greater than
or equal to `TEMP_DISK_MIN_FREE_BYTES`. Low space and ENOSPC normalize to
`TEMP_STORAGE_UNAVAILABLE`, use the existing maximum-three-attempt exponential queue backoff, and
retain per-attempt cleanup. This floor cannot predict provider file size or perfectly reserve
space across concurrent workers. Monitor both `/data` and the TEMP_DIR filesystem externally.
Stage 12.1 does not scan or delete pre-existing/orphaned artifacts.

## Backup, upgrade, and rollback

Do not copy only the live `.db` file: WAL mode also uses `.db-wal` and `.db-shm`. Prefer SQLite's
online backup API. For example, while the service is running, create a consistent backup in the
data volume:

```bash
docker compose exec musicbot python -c "import sqlite3; s=sqlite3.connect('/data/musicbot.db'); d=sqlite3.connect('/data/musicbot-backup.db'); s.backup(d); d.close(); s.close()"
```

Move the completed backup to protected storage. Also back up `/data/onthespot` and deployment
configuration/secrets through a secure operator process. The DB contains users, Tracks/sources,
queues/history, Telegram file IDs, and deep links; audio remains in Telegram. Deleting cache-chat
messages can invalidate stored Telegram file references.

Safe single-instance upgrade procedure:

1. create and verify DB/provider/config backups;
2. stop the one service (there is no zero-downtime SQLite replica handoff);
3. build or pull a versioned image;
4. run `docker compose run --rm --no-deps musicbot alembic upgrade head` explicitly;
5. run `docker compose run --rm --no-deps musicbot python -m app.main --check`;
6. start the service and verify logs plus health/readiness if enabled.

An Alembic downgrade is not automatically a safe rollback after the newer app has written data.
Restore the verified pre-upgrade backup with the matching application image when rollback is
required.

## Security and resource notes

The default service drops all Linux capabilities and enables `no-new-privileges`; it needs no
privileged mode. Configure CPU/memory limits for observed workloads, leaving enough memory and
scratch disk for the largest expected transcode. Do not assume one universal limit. Keep outbound
Internet access for Telegram/providers and keep inbound API access private.

Known Stage 12.1 limitations: one SQLite application instance; no stale-artifact watchdog/orphan
recovery; no scheduled backups or restore automation; no persistent security audit history; no
PostgreSQL, Redis, distributed workers, or multi-replica operation; no TLS automation; and no
guarantee that the reserve floor prevents every concurrent ENOSPC event.
