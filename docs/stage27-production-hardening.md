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
`MAX_BATCH_ITEMS` before child creation. Stage 25 still owns candidate ordering,
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

## Recovery and lease invariant

Existing queue recovery reclaims expired technical leases and existing lifecycle
recovery reconciles provider attempts and retries. Worker heartbeats renew the
lease while a bounded operation runs; capacity is therefore released after
recovery and no finite step can remain technical `RUNNING` forever.

## Diagnostics

`SystemDiagnosticsService` is a bounded read model over queue counts, worker
usage, recent terminal failures, expired claims, and TEMP_DIR pressure. It is
used by local operational tooling and the OWNER/ADMIN-only `/system` command.
`/job <id>` shows one bounded technical job summary and provider-attempt events.
No credentials, tokens, cookies, paths, or raw upstream payloads are returned.

## Acceptance and non-goals

Focused tests cover atomic claims, per-user fairness, provider limiter
cancellation/isolation, storage pressure, timeout taxonomy, lease recovery,
diagnostics authorization/redaction, and collection limits. Full completion also
requires the canonical locked/static/test and Linux production validation gates;
external provider/Telegram credential smoke remains separately classified.
