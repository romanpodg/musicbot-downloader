# Stage 23: album, playlist, and batch downloads

Stage 23 snapshots an album or playlist into a durable `BatchDownloadRequest`
and ordered `BatchDownloadItem` rows. Duplicate entries are retained and the
stored positions are immutable; provider membership is not re-read during
execution.

Each admitted item is handed to the existing Stage 21 `DownloadRequest` and
`DownloadJob` path. Stage 21 owns queue fairness, leases, retries, recovery,
workspace handling, and delivery. Stage 23 only coordinates admission and
derives aggregate status. The batch stores the Stage 22 requested preference
snapshot; every child resolves its own effective profile from that snapshot and
its capabilities.

Terminal semantics are `COMPLETED` when all children succeed, `PARTIAL` when at
least one succeeds and another fails or is skipped, `FAILED` when no child
succeeds (including expansion failure), and `CANCELLED` after an explicit
durable cancellation. Cancellation calls the normal Stage 21 cancellation API;
successful children remain successful. Retry-failed creates one linked retry
batch and never resurrects terminal jobs.

Limits are configurable through `MAX_BATCH_ITEMS` (`max_batch_items`) and
`MAX_ACTIVE_BATCHES_PER_USER` (`max_active_batches_per_user`). Child deliveries
retain the existing `PRIVATE_USER` routing policy, and completion timing is not
strictly collection-ordered.

Stage 23 intentionally does not provide archive/ZIP delivery, persistent media
or Telegram caches, history UI, cross-provider fallback, playlist
synchronization, or strict delivery ordering.
