# Stage 21 — Reliable Download Lifecycle

Stage 21 adds a durable user-intent lifecycle around the existing Stage 7/9
technical queue and Telegram outbox. `download_requests` is keyed by the
opaque confirmation token; `download_lifecycle_jobs` is one technical job per
request; and `download_deliveries` is one final declared target per job.

Lifecycle transitions are centralized in `DownloadLifecycle` and
`DownloadLifecycleService`. Claims are atomic SQLite updates with worker leases;
heartbeats renew leases, and expired leases enter persisted `RETRY_WAIT` (or
`FAILED` after the attempt limit). Retry timestamps use bounded exponential
backoff with jitter and are requeued through the normal fair queue. Failure
classification distinguishes retryable network/provider/Telegram failures from
media, processing, and permanent delivery failures.

The existing Telegram delivery worker reconciles its durable outbox state into
the lifecycle. A job is successful only after a `DELIVERED` receipt is recorded;
normal USER routing remains `PRIVATE_USER`. Cancellation is durable and checked
at safe points. Per-job temporary workspaces are isolated and conservatively
cleaned, including orphan cleanup.

Telegram’s external send and SQLite commit still have an unavoidable crash
window. The lifecycle therefore provides idempotent admission and best-effort
deduplicated delivery, not mathematically exactly-once external side effects.

