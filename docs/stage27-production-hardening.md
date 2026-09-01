# Stage 27 — Production Limits, Resource Safety & Observability

## Goal and boundary

Stage 27 hardens the existing single-runtime SQLite execution path. It does
not add a queue, lifecycle, provider-health store, recovery daemon, or external
metrics stack. Stage 21–26 authorities remain unchanged.

## Authority map

`QueueManager`/`AsyncWorkerPool` and the durable queue repositories own global
download/upload capacity and leases. `DownloadJobRepository.claim` applies the
per-user active download policy atomically while the worker holds SQLite's
reserved write lock. Stage 23 owns immutable collection snapshots and applies
`MAX_BATCH_ITEMS` before child creation. The legacy album-card route applies
that same ceiling before it persists its durable album snapshot, so neither an
album callback nor its older coordinator can create a partial collection.
Stage 24 historical album/playlist repeat rebuilds a new Stage 23 snapshot and
therefore checks the current ceiling. An oversized immutable history snapshot
is rejected before any new children exist. `Retry failed` instead snapshots
only the persisted retryable failed subset; it keeps the Stage 23 retry contract
when that subset fits, even if a later configuration is lower than the original
collection size. Stage 25 still owns candidate ordering,
fallback, and account health. `ProviderRateLimiter` only delays operations at
the provider boundary and never records a provider failure. `DownloadArtifactManager`
and `TemporaryDiskGuard` own artifact containment and storage preflight.

## Configuration and policies

Worker maxima (`DOWNLOAD_WORKERS_MAX`, `UPLOAD_WORKERS_MAX`) are the global
capacity limits; runtime desired counts cannot exceed them. `PER_USER_ACTIVE_DOWNLOAD_LIMIT`
defaults to 2. `MAX_BATCH_ITEMS` defaults to 100. `TEMP_DIR_MAX_BYTES` and
`TEMP_DISK_MIN_FREE_BYTES` protect both configured usage and filesystem reserve.
Provider pacing is configured with `PROVIDER_RATE_LIMIT_INTERVAL_SECONDS` and
`PROVIDER_MAX_CONCURRENT_OPERATIONS`. Download, ffprobe, transcode, upload, and
Telegram delivery boundaries all have finite timeouts. `STUCK_JOB_THRESHOLD_SECONDS`
is a diagnostic threshold. Invalid values fail settings validation.

## Storage pressure

Before new artifact-producing work enters provider resolution, the worker checks
both TEMP_DIR usage and filesystem reserve. If either is blocked it runs the
existing `StaleArtifactCleanupService`, then checks again. Only safely stale,
unowned roots can be reclaimed: active roots, active processing, active upload
ownership, and recoverable upload artifacts remain protected by the existing
registry and durable upload query. If capacity remains unsafe, the job follows
the established retry/defer path with `TEMP_STORAGE_UNAVAILABLE`. A Telegram
artifact-cache hit is delivered by the delivery worker without this acquisition
preflight and remains deliverable during local storage pressure.

## Recovery and lease invariant

Existing queue recovery reclaims expired technical leases and existing lifecycle
recovery reconciles provider attempts and retries. Worker heartbeats renew the
lease while a bounded operation runs; capacity is therefore released after
recovery and no finite step can remain technical `RUNNING` forever. The 900s
technical lease is renewed every 60s by the active worker while bounded
provider, FFmpeg/ffprobe, transcode, upload, or Telegram operation tasks are
still alive; legitimate long bounded work is therefore not reclaimed as dead.

## Diagnostics

`SystemDiagnosticsService` is a bounded read model over queue counts, worker
usage, recent terminal failures, expired claims, TEMP_DIR pressure, provider
health, and local provider pacing. It is used by local operational tooling and
the OWNER/ADMIN-only `/system` command. Provider health comes exclusively from
the Stage 25 provider-health authority. The limiter separately reports local
active operation count and `ready`/`waiting` throttle state: a healthy provider
can therefore be locally throttled without an invented health degradation.
The bounded conditions identify normal queued work, global capacity saturation,
per-user concurrency pressure, local throttling, storage pressure, and
expired/stuck work.
`/job <id>` shows one bounded technical job summary and provider-attempt events.
No credentials, tokens, cookies, paths, or raw upstream payloads are returned.

## Acceptance and non-goals

Production validation derives the only expected Alembic revision from Alembic's
repository script directory, requires exactly one repository head, verifies the
production image has the same single head, and then checks production databases
and backups against it. This remains correct after future linear migrations.

Focused tests cover atomic claims, per-user fairness, provider limiter
cancellation/isolation, storage cleanup-before-defer and active ownership,
timeout taxonomy, lease recovery, diagnostics authorization/redaction, and
collection limits. Full completion also requires the canonical locked/static/test and Linux production validation gates;
external provider/Telegram credential smoke remains separately classified.
