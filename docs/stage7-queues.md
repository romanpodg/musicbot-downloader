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

No `UploadExecutor` is configured in production Stage 7, so application startup does not start queue
processing. Tests inject controlled executors. This avoids silently accumulating artifacts and does
not pretend that Telegram delivery exists.
