# Stage 23 — Album, Playlist, and Batch Downloads

Stage 23 snapshots a provider collection into a durable `BatchDownloadRequest`
and ordered `BatchDownloadItem` rows. Duplicate entries are retained and the
stored positions are immutable; provider membership is not re-read during
execution. Album and playlist expansion are performed inside the isolated
OnTheSpot worker through its provider playlist/album registry. The resulting
membership is copied into the durable snapshot; provider changes after
admission cannot mutate an active batch.

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

Startup runs Stage 21 recovery first and then reconciles every persisted
non-terminal batch. Cancellation is durable: pending items are skipped, active
child jobs receive the normal Stage 21 cancellation request, and the aggregate
becomes `CANCELLED` after active children reach terminal states. Retry-failed
creates one linked batch using the original frozen requested preferences.

Stage 23 intentionally does not provide archive/ZIP delivery, persistent media
storage, history UI, playlist synchronization, or strict delivery ordering.
Each playlist child still uses the normal Stage 21 lifecycle, Telegram cache,
and Stage 25 provider/account resolution path.
