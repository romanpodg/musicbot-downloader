# Stage 24 — Download history and Telegram artifact cache

Stage 24 projects the durable Stage 21 `download_requests`, lifecycle jobs, and
deliveries into a bounded per-user `/history` view. Top-level history combines
single-track requests with top-level Stage 23 batches; child requests are
suppressed. Entries are ordered by `(created_at DESC, id DESC)` and use an
opaque timestamp/id cursor. Telegram callbacks re-check persisted ownership.

`Send again` clones the original source and effective Stage 22 profile into a
new request with `replay_of_request_id`; it then follows normal admission,
job, and delivery handling. Current settings do not alter replay semantics.

The bot-wide artifact cache is an execution optimization inside the Stage 21
delivery worker. Its SHA-256 key contains provider/media ID, effective quality
and format, delivery mode, metadata/cover flags, and processing version `1`.
Successful deliveries populate it; a permanent invalid-file response
invalidates the row and falls back to the normal pipeline, while temporary
errors retain the row. Pruning removes old invalid rows and deterministic LRU
excess without touching history. Batch children automatically use the same
cache service.

Playlist downloads, cross-provider identity/fallback, persistent local media,
archives, history deletion, and Stage 25+ functionality remain out of scope.
