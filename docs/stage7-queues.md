# Stage 7 queue lifecycle

Stage 7 controls persistence, scheduling, retry timing, cancellation, worker lifecycle, and artifact
handoff. It delegates media decisions and execution to the existing Stage 6 `DownloadPipeline`.

## State transitions

```text
DownloadJob
  QUEUED -> RUNNING -> SUCCEEDED
                    -> QUEUED (bounded delayed retry)
                    -> FAILED
                    -> CANCELLED

UploadJob
  QUEUED -> RUNNING -> SUCCEEDED -> release artifact
                    -> QUEUED (retry; preserve artifact)
                    -> FAILED -> release valid owned artifact
                    -> CANCELLED -> release valid owned artifact
```

Attempt counts increment only in the atomic claim that starts actual execution. `available_at`
persists retry backoff across restarts. Claims use `queued_at, id` FIFO ordering and compare status
in the same update. Completion, retry, heartbeat, and handoff operations are fenced by `lease_owner`.

## Ownership

```text
Stage 6 DownloadResult
  -> DownloadWorker owns artifact
  -> validate TEMP_DIR containment and non-empty final output
  -> commit unique UploadJob + DownloadJob SUCCEEDED
  -> UploadQueue owns artifact
  -> UploadExecutor confirms delivery
  -> commit UploadJob SUCCEEDED
  -> DownloadArtifactManager.release(artifact_job_id)
```

The persisted artifact path is relative to `TEMP_DIR`. Before upload or release it must resolve to
the matching `<artifact_job_id>/output/final.*` path and remain contained. Before delivery it must
also be a non-empty regular file. Invalid external paths are neither read nor deleted.

Terminal cleanup is commit-then-release. This prevents a retryable DB job from pointing at an
artifact already deleted, but a crash in that small window can leak a file. SQLite and the filesystem
are not atomically coordinated. External delivery and the UploadJob success commit are also not
atomic, so a crash between them can produce duplicate delivery on retry. These recovery windows are
intentionally deferred beyond Stage 7.

## Runtime pools

`runtime_settings` is desired state. Environment defaults bootstrap it and environment maxima bound
it. Download and upload pools reconcile independently. Excess workers finish their current job and
retire before claiming again. Idle workers wait on an in-process event with bounded DB polling as a
restart-safe fallback.

Stage 7 defines the generic `UploadExecutor`; Stage 8 now supplies the production
`TelegramCacheUploadExecutor`. `app.main` still starts no queue daemon or Telegram update loop.
An explicit application owner composes the Stage 8 executor and passes it to `UploadWorkerBackend`,
which lets the existing QueueManager start both pools without coupling queue logic to aiogram.

## Stage 7.1 SingleFlight admission

`SingleFlightService` is the normal future user-request admission boundary. The lower-level
`DownloadQueueService.submit()` remains available for maintenance and queue-focused tests. A flight
key is exactly `(track_id, quality_profile)`. Provider identity, TrackSource, URL, requester,
authentication state, and DownloadPlan remain Stage 4/5/6 runtime concerns.

```text
submit
  -> BEGIN IMMEDIATE
  -> reconcile matching flight if stale
  -> eligible flight: attach durable subscriber (capacity is irrelevant)
  -> no flight: enforce DownloadJob capacity
  -> atomically insert DownloadJob + download_flights + job_subscribers
  -> commit before returning IDs
```

`download_flights` has unique constraints on `(track_id, quality_profile)` and `download_job_id`.
`job_subscribers` uses UUID string IDs, a restrictive DownloadJob foreign key, a stable status CHECK,
and `(download_job_id, request_key)` uniqueness. SQLite permits multiple `NULL` values in that
constraint, so submissions without a key always create a subscriber; the same non-null opaque key
within one shared job is idempotent. Request keys are limited to 128 characters and are not logged.

## Subscriber and flight lifecycle

```text
WAITING --UploadJob SUCCEEDED--> READY
WAITING --shared technical failure--> FAILED
WAITING --explicit/shared cancellation--> CANCELLED
```

Download success alone leaves subscribers `WAITING` while the unique UploadJob is queued, running,
or retrying. Download and upload retries reuse the same job, flight, and subscribers. Bulk terminal
updates target only `WAITING`, preserving explicit cancellation precedence. `READY` is the generic
shared-result boundary and does not assert Telegram delivery.

The active flight remains from DownloadJob admission through UploadJob terminal state, then is
deleted. Job and subscriber history is retained. This is active-work deduplication, not a permanent
cache. Stage 8's Telegram file-cache/delivery foundation will handle completed-result reuse.

Cancelling one subscriber does not affect work while another subscriber waits. Cancelling the last
waiter transactionally removes the eligible flight before requesting DownloadJob or UploadJob
cancellation. Queued work becomes terminal immediately; running work receives `cancel_requested`
and an attached QueueManager cancels the local operation task cooperatively. This ordering prevents
a simultaneous new request from joining work already committed to cancellation.

## Crash reconciliation and waiting

Submission reconciles the matching flight before attachment. Workers reconcile expired leases and
terminal transitions in the same database transaction; QueueManager also reconciles at startup and
on its normal control loop. Reconciliation maps terminal DownloadJob/UploadJob state to subscribers
and closes the flight idempotently, repairing a crash after the terminal job commit but before
propagation. A cancelling job is never eligible for attachment.

`SingleFlightService.wait()` reads durable state and uses one process-local `asyncio.Condition` to
wake waiters after terminal changes. A bounded reload interval covers another service instance or a
restart; notification objects are never authoritative. Timing out or cancelling the Python waiter
does not cancel the subscriber. Only `cancel_subscriber()` changes subscription state.

This architecture targets one application process, multiple async workers, and one SQLite/WAL
database. It deliberately includes no Redis, broker, distributed locks, or multi-host coordination.
