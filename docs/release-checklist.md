# Downloader release checklist

This checklist is the Stage 12.4 acceptance gate for the downloader application. Record command
output in the release review; a checked box without executed evidence is not a pass. Do not add
Telegram or provider credentials to CI logs, artifacts, images, or this document.

Release verdicts are:

- **READY**: every mandatory deterministic gate passed in an actual Linux/container environment,
  and any applicable external credential checks passed.
- **READY WITH EXTERNAL VERIFICATION PENDING**: every mandatory deterministic gate passed in
  Linux/containers, but optional real Telegram/provider checks were not run.
- **NOT READY**: a mandatory gate failed, or the production image was not built and exercised on
  Linux. Stage 12.4 remains incomplete for this verdict.

## Mandatory

### Candidate and host

- [x] Record `git rev-parse HEAD`, `git status -sb`, and `git log --oneline --decorate -8`
  from the preserved release-candidate working tree.
- [x] Record Python requirement `>=3.12,<3.13`, Alembic head, and OnTheSpot commit
  `8ed6cf33ef772e6569d5014237e0fb4ce8b9e45d`.
- [x] `uv lock --check` passes before synchronization.
- [x] `uv sync --locked --extra dev --extra onthespot` passes.
- [x] `uv lock --check` passes after synchronization.
- [x] `uv run ruff format --check .`, `uv run ruff check .`, and `uv run mypy app` pass.
- [x] `uv run pytest -m "not external" -ra` passes; record passed, skipped, deselected, and failed
  counts and explain every skip.
- [x] Alembic upgrade/check/current/heads, `app.main --help`, network-free `app.main --check`, ops
  help/status, and `git diff --check` pass against an isolated database.

### Linux and production image

- [x] `docker compose config --quiet` passes with a controlled fake/non-secret `.env`.
- [x] `docker build -t musicbot-downloader:stage12.4 .` builds the default production target.
- [x] Image inspection records OS/architecture and approximate size; no untested multi-arch claim.
- [x] The image runs Python 3.12, FFmpeg, and ffprobe and starts as UID/GID `10001:10001`.
- [x] Runtime image contains app code, locale files, Alembic config/migrations, and no `.env`, test
  tree, local DB/backup, uv, Ruff, mypy, or pytest dependency group.
- [x] `python -m app.main --help`, `--check`, ops help/status, and fresh Alembic migration execute
  inside the image without Telegram/provider traffic or an HTTP bind.
- [x] `/data`, OnTheSpot/XDG state paths, and `TEMP_DIR` have the documented ownership; `/app` and
  `/root` are not writable by the runtime user.
- [x] Read-only-root preflight passes with only `/data` and controlled temporary storage writable.
- [x] Fresh DB installation, representative `0010 -> 0011`, isolated `0011 -> 0010 -> 0011`,
  schema mismatch refusal, WAL, foreign keys, and 5000 ms busy timeout are verified.
- [x] Users, Tracks, TrackSources, queue/history and subscriber rows, Telegram cache metadata,
  worker settings, Deep Links, and audit rows survive container recreation with the same `/data`;
  completed audio is absent from `/data`.

### Filesystem, lifecycle, recovery, and backup

- [x] Linux top-level and inner-symlink cleanup tests pass and every external target survives.
- [x] A final-output/upload symlink escaping `TEMP_DIR` is rejected before upload.
- [x] A live-WAL online backup succeeds during writes, has mode `0600`, passes integrity/schema
  validation, and rejects existing, symlink, source-equal, and failed partial destinations.
- [x] Manual offline restore drill preserves the post-backup original, atomically installs the
  validated backup, restores restrictive ownership/mode, and passes Alembic/check/status plus
  representative-row verification.
- [x] First/second lock holders, stale lock-file contents, graceful release, SIGKILL release, and
  partial-startup release pass on POSIX. Offline mutating ops refuse while the runtime lock is held;
  status and online backup remain usable.
- [x] SIGTERM stops the supervisor, DB, cleanup watchdog, API, polling harness, and isolated child
  without an orphan. Critical-service failure is fatal; an individual queue worker failure remains
  contained by its pool.
- [x] Expired DownloadJob recovery, retained Upload artifact, ephemeral artifact loss, exact-cache
  rescue, stale cleanup, owned/active preservation, error isolation, and recovery-before-workers
  tests pass on Linux.

### Application and security regressions

- [x] Internal API private/default bind, bearer 401/success behavior, no CORS, body bounds, SSRF
  rejection, startup/shutdown supervision, and Deep-Link Track registration pass without secrets.
- [x] Fake Bot/Internal API/provider tokens, refresh tokens, cookies, and ARL values do not appear
  in captured logs or audit rows.
- [x] Transactional role/worker/Deep-Link audit tests pass; audit is bounded, append-only through
  application APIs, and does not record the normal download/cache hot path.
- [x] Cache-first behavior is independent of provider health/auth; 100-user Track fanout, second
  wave cache reuse, Album per-Track deduplication, and Deep-Link Track/Album fanout pass.
- [x] Dynamic auth, input-provider neutrality, fresh Stage 4-6 provider checks, strict quality
  policy, disk reserve, ENOSPC normalization, and no persistent media-cache regressions pass.
- [x] Compose remains one unprivileged service with `/data` persistence, no replicas, dropped
  capabilities, `no-new-privileges`, no public port, and documented restart/readiness semantics.
- [x] Tracked-file/build-context secret scan passes; a real `.env`, DBs, backups, temp artifacts,
  developer caches, and provider state are not copied into the image.
- [x] Record the pinned OnTheSpot commit, installed distribution metadata, bundled GPL-2.0 license
  text, and the need for operator/legal review before distributing an image or binary.

The repository automation for these gates is:

```bash
docker build -t musicbot-downloader:stage12.4 .
docker build --target validation -t musicbot-downloader:stage12.4-validation .
cp .env.example .env  # CI/local fake fixture only; configure real secrets only for deployment
bash scripts/validate-production.sh
```

The validation image is opt-in and contains development dependencies. The default/final `runtime`
target does not inherit it and is the only release image.

## Optional external

- [ ] Telegram `getMe` with the real downloader bot.
- [ ] Private cache-chat upload permission and a harmless test message/file lifecycle.
- [ ] Private `/start`, OWNER/ADMIN access, and delivery using a permitted test artifact.
- [ ] Configured provider account initialization and metadata/search/source checks.
- [ ] Tokenless public-provider metadata-only smoke, when safely available.
- [ ] One operator-authorized live Track flow using media the operator is permitted to download.

External checks must be opt-in (`pytest -m external` plus their documented environment variables).
The four external tests cover Telegram gateway/cache-chat access and OnTheSpot authenticated Track,
authenticated search, and tokenless public metadata boundaries. They are not part of required CI.

## Release record

Record the final revision, UTC date, image ID/platform/size, exact host and Linux test counts,
mandatory failures/fixes/re-runs, optional checks performed, remaining limitations, and one verdict
from the definitions above. Never mark the Stage complete from a workflow file or static
Dockerfile inspection alone; retain the actual workflow/container logs as evidence.

### 2026-08-20 local deterministic validation

- Candidate: `4402af8` plus the preserved uncommitted Stage 12.4 working tree.
- Host: Python 3.12.13; 473 passed, 4 platform-inapplicable skips, 4 external deselected, 0 failed.
- Production image: `linux/amd64`, 342,288,176 bytes, Python 3.12.14, UID/GID
  `10001:10001`.
- Linux validation image: 477 passed, 0 skipped, 4 external deselected, 0 failed.
- Script result: `STAGE12_4_CONTAINER_VALIDATION=PASS`.
- Validation fixes: the validation target now includes its release-test assets; the Stage 9
  regression writes only under pytest temporary storage; the synthetic FFmpeg fixture uses
  deterministic stereo input so AAC bitrate validation is meaningful.
- External Telegram/provider credential checks were not executed. Deterministic verdict:
  `READY WITH EXTERNAL VERIFICATION PENDING`.

### 2026-08-21 local deterministic validation

- Candidate: `6f8e625` plus the preserved Stage 12.4 working tree documented by `git diff`.
- Host: Python 3.12.13; 473 passed, 4 platform-inapplicable skips, 4 external deselected,
  0 failed. The skipped FFmpeg and symlink cases passed in the Linux image.
- Production image: `sha256:2ae1e818da453731e9937555e6e748a4cfbfcde295e7902def786f33e6aea9fb`,
  `linux/amd64`, 342,288,472 bytes, Python 3.12.14, UID/GID `10001:10001`.
- Linux validation image: 477 passed, 0 skipped, 4 external deselected, 0 failed.
- Script result: `STAGE12_4_CONTAINER_VALIDATION=PASS`.
- Validation fixes: Git Bash now preserves Linux Docker arguments while converting the fixture
  bind path; the durable recreation fixture includes queue/subscriber history and all required
  SQLite pragmas; schema mismatch, runtime tool absence, locale files, and installed OnTheSpot
  license metadata are explicit image gates; timeout tests leave bounded Windows process-start
  headroom while still forcing the ten-second fake operation to time out.
- External Telegram/provider credential checks were not executed. Deterministic verdict:
  `READY WITH EXTERNAL VERIFICATION PENDING`.

## Licensing / distribution review

The locked direct runtime set resolves aiogram (MIT), aiosqlite (MIT), Alembic (MIT), FastAPI
(MIT), Pydantic and pydantic-settings (MIT), SQLAlchemy (MIT), and Uvicorn (BSD-3-Clause) from their
installed distribution metadata. The optional production `onthespot` extra installs the exact
commit `8ed6cf33ef772e6569d5014237e0fb4ce8b9e45d` (documented upstream v1.8.1; installed package
metadata currently reports `0.1.0`) and bundles `onthespot-0.1.0.dist-info/licenses/LICENSE`, whose
text is GNU GPL version 2.

The production image distributes OnTheSpot and its transitive dependencies. The parent downloader
does not copy the upstream repository into its own source tree and communicates with the isolated
child over bounded JSON Lines, but process isolation must not be treated as automatically removing
license obligations. Preserve installed distribution license metadata and obtain an operator/legal
review before publishing images or binaries. This inventory is factual release input, not legal
advice and not a definitive compliance conclusion. This is not legal advice.
