# Production deployment and release validation (Stage 12.4)

> **Enforced single-instance constraint:** An OS advisory lock at
> `<SQLite database>.instance.lock` permits at most one downloader runtime against a database on
> this host. A second runtime exits before database recovery, workers, listeners, providers, or
> polling. This is not distributed coordination; do not run replicas across hosts.

The production image packages the existing `python -m app.main` runtime as one service. The process owns
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
docker build -t musicbot-downloader:stage12.4 .
```

Stage 12.4 adds an opt-in `validation` target derived from the production runtime. It installs the
locked dev group plus the test and release-validation assets needed for Linux acceptance; the
final/default `runtime` target does not inherit that layer. Build and execute both targets with a
fake, non-secret `.env`:

```bash
docker build -t musicbot-downloader:stage12.4 .
docker build --target validation -t musicbot-downloader:stage12.4-validation .
cp .env.example .env
bash scripts/validate-production.sh
```

The smoke script fails fast, uses fresh uniquely named Docker volumes, removes only its own
containers/volumes, and performs no Telegram/provider request or commercial media download. It
exercises image identity/tools, fresh and upgrade/downgrade migrations, read-only-root preflight,
persistent durable rows, online WAL backup, manual restore, POSIX process locks, and the complete
non-external Linux suite. Its output is release evidence, not a committed machine result. See
[`docs/release-checklist.md`](release-checklist.md) for the mandatory and optional external gates.

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
| `TEMP_CLEANUP_INTERVAL_SECONDS` | Cleanup watchdog interval; default `600`, bounded to 60–86400 seconds |
| `TEMP_ARTIFACT_STALE_AFTER_SECONDS` | Minimum orphan age; default `3600`, at least the cleanup interval |
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
local databases, provider config, temporary media, caches, Git metadata, IDE state, and coverage
artifacts; `.env.example` is the only safe exception. The test tree is available to the
opt-in Docker validation target but is never copied into the final runtime image.

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
filesystem/media prerequisites; acquire the database-adjacent instance lock; create the database
engine; verify the Alembic head; reconcile
the configured OWNER; create Telegram and obtain bot identity; compose provider/runtime services;
recover expired DownloadJob and UploadJob leases; recover expired delivery and album-item leases;
validate non-terminal UploadJob artifacts and perform exact ACTIVE-cache rescue; reconcile
SingleFlight subscribers and terminal Album aggregates without sending Telegram messages; run one
conservative stale-artifact sweep; start Download/Upload, delivery, and album workers; start the
supervised cleanup watchdog; start the optional Internal API; create Telegram polling; mark ready.
No worker can claim work and readiness cannot become true before recovery and the startup sweep
finish.

The lock uses `fcntl.flock` on production POSIX systems and `msvcrt.locking` for Windows
development/tests. File contents (PID and UTC start time) are diagnostics only: file existence or a
stale PID never establishes ownership. The OS releases the advisory lock on graceful close and
process death. Startup cleanup releases it after partial failure. `app.main --check` never holds the
runtime lock and remains safe beside the live service.

SIGTERM/SIGINT cancels polling and runs bounded worker shutdown, stops uvicorn, closes the
OnTheSpot child (terminate then kill after its existing bound if needed), closes Telegram, and
disposes the DB engine. Individual Download/Upload worker exceptions remain contained by their
existing pools. Unexpected termination of Telegram polling, the enabled Internal API, or the
cleanup watchdog is fatal;
the process shuts down all resources and exits nonzero so the service manager can restart it.
Deterministic config/schema errors will keep failing under `restart: unless-stopped`; run `--check`
and inspect logs before startup.

## Health and readiness

When the Internal API is enabled:

- `GET /internal/v1/health` is unauthenticated liveness and returns only `{"status":"ok"}`;
- `GET /internal/v1/ready` is unauthenticated readiness, returning only
  `{"status":"ready"}` or HTTP 503 `{"status":"not_ready"}`.

Readiness means schema validation, crash recovery, the startup cleanup sweep, worker startup,
listener startup (when enabled), and polling task creation completed. It does **not** mean any provider is authenticated or able to
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

The cleanup watchdog scans only direct UUID-style children of `TEMP_DIR`. It never scans `/data`,
OnTheSpot state, or database files. A root is deleted only when it is not process-active, is not
owned by any non-terminal UploadJob, is older than `TEMP_ARTIFACT_STALE_AFTER_SECONDS`, and passes
containment/layout checks. Young orphans and queued/running/retry-delayed upload artifacts are
preserved regardless of age. Unknown entries and top-level symlinks are ignored. Recursive removal
does not follow inner symlinks; only the link entry is removed with its controlled tree, leaving an
external target untouched. Candidate races and item-level filesystem errors are isolated. The
periodic scan/deletion runs off the event loop and does not weaken the Stage 12.1 free-space floor:
cleanup may let a later retry pass, but acquisition still checks the configured reserve.

## Operator audit and recovery CLI

Run the CLI in the same image/environment and with the same `DATABASE_URL`/`TEMP_DIR` as the
service. All output is bounded; `--json` emits typed primitives without ORM representations.

```bash
python -m app.tools.ops status [--json]
python -m app.tools.ops audit list --limit 50 [--before-id ID] [--event EVENT] [--actor-user-id ID] [--json]
python -m app.tools.ops recovery inspect [--json]
python -m app.tools.ops recovery run [--json]
python -m app.tools.ops artifacts scan [--json]
python -m app.tools.ops artifacts cleanup [--json]
python -m app.tools.ops backup create /protected/backups/musicbot-YYYYMMDD.db [--json]
```

`status` is DB/config/filesystem-only. It reports current/head schema, runtime-lock state, bounded
Download/Upload counts, active SingleFlight/waiting subscriber counts, delivery/album/cache counts,
persisted desired workers, TEMP_DIR free/reserve bytes, and audit count/latest timestamp. An
external CLI cannot see asyncio worker tasks, so actual workers are explicitly unavailable. It
never probes Telegram, providers, OnTheSpot, FFmpeg, or HTTP.

`audit list` is read-only, ordered by UTC occurrence then ID descending, defaults to 50, and rejects
limits above 200. Audit rows distinguish `TELEGRAM_USER`, `INTERNAL_API`, `LOCAL_OPERATOR`, and
`SYSTEM`. The audit is append-only through application APIs and records only successful promotion,
demotion, desired-worker, Deep-Link create/revoke, startup/manual recovery, manual cleanup, and
backup transitions. It stores internal IDs and small allow-listed summaries, never public
Deep-Link tokens, raw URLs, Bot/Internal API tokens, Authorization headers, provider credentials,
cookies, raw Telegram updates, file contents, or exception tracebacks. It adds no download history.
Periodic cleanup remains structured-log-only. Audit retention is not automated.

Role, desired-worker, and Deep-Link mutations append their audit row in the same SQLite transaction
as the state transition; no-op/idempotent calls add nothing, and audit insertion failure rolls the
mutation back. Startup recovery spans existing independent recovery transactions. Its bounded
SYSTEM summary is appended afterward; audit failure is logged and does not replay already committed
recovery. Manual recovery and cleanup append one LOCAL_OPERATOR summary after success.

`recovery inspect` and `recovery run` require the application to be stopped and acquire the runtime
lock. Inspect is dry-run. Run invokes the Stage 12.2 `CrashRecoveryService`; neither starts workers,
Telegram, the Internal API, OnTheSpot, or providers. Offline exact-cache rescue uses a persisted bot
identity only when it is unambiguous; otherwise it conservatively skips cross-bot rescue. Artifact
scan and cleanup are also offline-only because another process cannot observe the live
`ActiveArtifactRegistry`. Scan uses the exact Stage 12.2 classifier without deletion. Cleanup runs
one sweep with the configured ownership, containment, symlink, and stale-age rules; there are no
force/ignore/path override flags.

## Crash and atomicity semantics

On process crash or SIGKILL, SQLite/WAL durable state survives. At the next startup, only expired
leases are reclaimed under their existing bounded-attempt/cancellation policies; non-expired leases
remain unchanged. Download retries use a fresh Stage 4–6 resolution and current provider auth. An
artifact created before UploadJob handoff has no durable owner and is deleted only after becoming
stale. An UploadJob with a surviving valid artifact is preserved and can retry. If container
replacement or host policy removed ephemeral `/tmp/musicbot`, the pending UploadJob fails safely,
waiting subscribers are reconciled, and a future user request may build fresh media. TEMP_DIR is
not backed up and is intentionally not moved into `/data`.

SQLite transitions are transactional, but SQLite, the filesystem, and Telegram cannot participate
in one atomic transaction. Stage 12.2 narrows recoverable windows; it does not promise exactly-once
external effects:

- an exact ACTIVE cache row committed before UploadJob completion rescues the upload without a new
  Telegram call;
- Telegram may accept a cache upload before its SQLite cache row commits, leaving no durable
  evidence; retry may upload it again and recovery never scrapes Telegram history;
- a user send may succeed before the delivery row commits DELIVERED, so retry may duplicate it;
- an album completion message has the same send-before-commit duplicate window;
- filesystem creation/release cannot commit atomically with the UploadJob transaction, so stale
  grace and durable ownership—not filesystem guessing—govern cleanup.

## Backup, upgrade, and rollback

Do not copy only the live `.db` file: WAL mode also uses `.db-wal` and `.db-shm`. The Stage 12.3
command uses SQLite's online backup API and can run while the service is live:

```bash
docker compose exec musicbot python -m app.tools.ops backup create /data/musicbot-backup.db
```

The destination must be explicit, must differ from the source, and must not already exist (including
symlinks). The command writes a restrictive `0600` temporary sibling, snapshots committed WAL state,
runs `PRAGMA integrity_check`, verifies the Alembic revision, and atomically publishes without
overwriting. Failure removes only its controlled partial file. The subsequent backup audit event is
in the live database and intentionally absent from the already completed snapshot.

Move the completed backup to protected storage. The SQLite backup includes only application data;
it excludes `.env`/secrets, `/data/onthespot`, and TEMP_DIR media. Also back up `/data/onthespot` and deployment
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

Safe manual restore (there is deliberately no automatic restore command): stop the application;
confirm an offline lock-requiring CLI command can acquire the instance lock; create a backup of the
current DB if possible; validate the candidate backup with `PRAGMA integrity_check` and its
`alembic_version`; replace the stopped DB using an operator-controlled atomic procedure; restore
UID/GID 10001 ownership and restrictive permissions; run `alembic current`; run
`python -m app.main --check`; then start one application instance and inspect logs/readiness.

For the named Compose volume, the replacement step can be performed by a stopped, one-off
non-root container. `validated-backup.db` below must already have passed integrity and revision
validation. The pre-restore copy is intentionally preserved until the restored service is
accepted:

```bash
docker compose stop musicbot
docker compose run --rm --no-deps musicbot python -m app.tools.ops recovery inspect
docker compose run --rm --no-deps musicbot sh -eu -c '
  umask 077
  cp /data/musicbot.db /data/musicbot.pre-restore.db
  if test -e /data/musicbot.db-wal; then
    cp /data/musicbot.db-wal /data/musicbot.pre-restore.db-wal
    rm -- /data/musicbot.db-wal
  fi
  if test -e /data/musicbot.db-shm; then
    cp /data/musicbot.db-shm /data/musicbot.pre-restore.db-shm
    rm -- /data/musicbot.db-shm
  fi
  cp /data/validated-backup.db /data/musicbot.db.restore
  chmod 600 /data/musicbot.db.restore
  mv /data/musicbot.db.restore /data/musicbot.db
'
docker compose run --rm --no-deps musicbot alembic current
docker compose run --rm --no-deps musicbot alembic check
docker compose run --rm --no-deps musicbot python -m app.main --check
docker compose run --rm --no-deps musicbot python -m app.tools.ops status --json
docker compose up -d musicbot
docker compose logs --tail 200 musicbot
```

Do not attempt replacement while the runtime lock is active. There is no restore command that can
bypass this offline procedure, and an Alembic downgrade is not a substitute for restoring a
validated pre-upgrade backup.

## Security and resource notes

The default service drops all Linux capabilities and enables `no-new-privileges`; it needs no
privileged mode. Configure CPU/memory limits for observed workloads, leaving enough memory and
scratch disk for the largest expected transcode. Do not assume one universal limit. Keep outbound
Internet access for Telegram/providers and keep inbound API access private.

Known limitations: SQLite/single-host operation only; Telegram side effects remain
at-least-once around SQLite commits; ephemeral container replacement can lose pending UploadJob
artifacts; cleanup deliberately waits for a conservative stale threshold; the reserve floor is not
a byte reservation; no scheduled backups, automatic restore, or automated audit retention; no
PostgreSQL, Redis, distributed workers, or multi-replica cleanup/recovery; no TLS automation; and no
guarantee that the reserve floor prevents every concurrent ENOSPC event.

## Licensing / distribution review

The production image includes third-party software with its own licenses, including the pinned
OnTheSpot dependency. Distribution obligations should be reviewed before publishing images or
binaries to third parties. This is not legal advice.

OnTheSpot is installed from exact commit `8ed6cf33ef772e6569d5014237e0fb4ce8b9e45d`
(documented upstream v1.8.1; its installed package metadata reports `0.1.0`) and ships a GNU GPL
version 2 license text under its installed distribution metadata. The application parent talks to
the isolated OnTheSpot child over bounded JSON Lines and no upstream source tree is copied into
this repository, but isolation must not be treated as automatically eliminating GPL obligations.
The runtime image also retains installed license metadata for transitive distributions.

The direct locked runtime dependencies declare or bundle MIT licenses for aiogram, aiosqlite,
Alembic, FastAPI, Pydantic, pydantic-settings, and SQLAlchemy, and BSD-3-Clause for Uvicorn. Review
the full transitive dependency set and the target distribution model before release. The factual
inventory and release sign-off gate are in [`docs/release-checklist.md`](release-checklist.md).
