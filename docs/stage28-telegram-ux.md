# Stage 28 — Telegram UX & Interaction Consistency

## Current implementation boundary

Stage 28 is presentation-only.  Telegram handlers remain adapters over the
existing Stage 21 lifecycle, Stage 23 `BatchDownloadService`, Stage 24 history
and preferences services, and Stage 25–27 execution boundaries.  They do not
select providers, restart technical jobs, decide cache behavior, or persist
history.

The repository previously had two overlapping ordinary-user paths:

- `app/telegram/ux_handlers.py` owned `/start`, search input, the Stage 18
  confirmation callback, and settings;
- `app/telegram/handlers.py` owned legacy track/album cards, batches, and
  history.

Stage 28 adds focused shared components rather than another lifecycle:

- `app/telegram/ux_presentation.py` maps durable lifecycle values to the eight
  stable user states (`Preparing`, `Waiting`, `Downloading`, `Processing`,
  `Sending`, `Delivered`, `Failed`, and `Cancelled`), supplies redacted error
  copy, and owns deterministic edit coalescing policy;
- `app/services/download_activity.py` is the sole owner-scoped read projection
  used by `app/telegram/ux28_handlers.py` for `/downloads` and its detail view;
- the home keyboard exposes Search, Recognize (the existing recognition/search
  prompt), Downloads, History, and Settings without changing the existing
  opaque callback namespaces.

Track admission callbacks now acknowledge immediately and turn the existing
track-card message into `Preparing…`; they do not emit a separate progress
message.  Stale admissions re-render a safe terminal/stale message rather than
starting duplicate work.  The lifecycle's confirmation-id uniqueness remains
the idempotency authority.

## Safety invariants

- `/downloads` has a bounded query (at most 50 records) and verifies the
  persistent requester identity for each detail request.
- The activity projection intentionally omits provider attempts, account IDs,
  file paths, exception text, cache implementation details, and SQL details.
- `DownloadStatusPresenter` never converts technical phase detail into fake
  percentages.  Cache hits may move directly from Preparing to Sending.
- `TelegramStatusUpdatePolicy` requires an injected clock, coalesces rapid
  non-terminal edits, and always permits a terminal update.
- Existing `dl18`, `h24`, `dp1`, and Stage 19 context/bot-scope validation are
  retained.  No callback contains credentials, filesystem paths, or provider
  account data.

## Deliberately incomplete work

The first Stage 28 increment does **not** yet wire durable status-message
references and the edit policy into lifecycle worker completion/recovery.  As a
result, terminal presentation is refreshed by the user-facing downloads view,
but it is not yet proactively reconciled after every worker transition or
restart.  Collection aggregate cards, full localized Stage 28 copy, and a
single end-to-end presenter bridge for history/settings/recognition also remain
to be completed before the stage can be accepted.

The missing wiring must extend the existing Telegram gateway and lifecycle
completion hooks; it must not add a second queue, status state machine, or
collection pipeline.
