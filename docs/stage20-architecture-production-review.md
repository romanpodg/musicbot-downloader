# Stage 20 — Architecture & Production Review

## Baseline

The reviewed baseline was clean commit `c33c48935e4011c4ceb53e3a6591d861d1252f20`
(`feat: channel context and delivery routing`). The working tree was clean and `git diff --check`
passed before changes.

## Confirmed defects and fixes

- Release validation ran `ffmpeg` and `ffprobe` without provisioning them, causing exit 127 on a
  clean Ubuntu runner. The workflow now installs the explicit `ffmpeg` package before retaining
  the existing version checks. `actions/checkout@v5` and `astral-sh/setup-uv@v7` use Node 24;
  uv remains pinned to `0.11.20`.
- Stage 14 state was keyed only by user ID. Ephemeral state is now keyed by the full immutable
  `TelegramContext` (`user_id`, `chat_id`, `chat_type`) throughout UX navigation, search input,
  progress, and download transitions. Integer state calls remain only as an explicit legacy
  compatibility form for pre-Stage-19 callers; Telegram handlers always pass context.
- Group `/search` depended on arbitrary following text, which Privacy Mode may hide. `/search
  <query>` now searches in one message. A no-argument group command sends `ForceReply`, and the
  search filter accepts only a reply to a bot message. Private two-step search remains unchanged.
- Recognition gave independent title and artist substring matches full credit for a raw combined
  query. Raw-query scoring now removes candidate artist tokens from the query and strictly scores
  the remaining title intent, while explicit structured fields keep direct component scoring.
  Exact combined intent and one-word titles remain ACCEPT; partial titles do not.
- Search stopped as soon as the global limit was filled, starving later providers. The service now
  queries every selected provider sequentially, then performs deterministic round-robin merging,
  preserving within-provider order and deduplicating provider identity before final truncation.
- USER-mode private delivery from a group could fail after admission with no actionable notice.
  A non-retryable Telegram permission failure now reaches terminal state once and attempts a
  localized origin-chat notice telling the user to open the bot privately and send `/start`.
  Notice failures are contained inside the worker.
- ACCEPT confirmations no longer present alternatives in Telegram; ASK_USER presents at most
  three alternatives. The underlying short-lived selection data remains compatible with existing
  Stage 18 callers.

## Architecture decisions left unchanged

Telegram remains a transport adapter over UX/application services. Recognition remains pure and
provider-neutral; adapters remain thin OnTheSpot boundaries. Download admission still enters the
existing durable delivery outbox, SingleFlight, queues, downloader, cache, and delivery worker.
No new downloader, queue, provider runtime, database state for UX, role system, or callback secret
payload was introduced. Global roles remain separate from chat policy, and delivery routing never
enters download code.

The documented `ChatRateLimitPolicy` is still an unused future seam; it has no production call
sites and no behavior was added in Stage 20.

## Tests and migration result

Focused regressions cover context isolation, cross-chat search filtering, Privacy Mode group flows,
raw-query recognition, provider fairness, bounded alternatives, and USER delivery permission
failure. The Stage 19 migration was exercised fresh, from predecessor `20260820_0011`, and through
downgrade/upgrade round trips. Chat policy/channel binding tables, legacy delivery backfill,
constraints, single Alembic head, and `alembic check` all pass; migration history was unchanged.
The isolated database ended at `20260825_0012` with `alembic current` equal to `alembic heads`.

## Validation evidence

Host deterministic suite: `668 passed, 4 skipped, 4 deselected` (`uv run pytest -m "not external" -ra`).
The skips are Windows-only FFmpeg/symlink capability skips. Ruff format, Ruff lint, mypy, and
`git diff --check` pass. Focused migration and Stage 14–20 regressions pass.

Linux/container validation: **PASS**. Fresh `musicbot-downloader:stage20` and
`musicbot-downloader:stage20-validation` images completed `scripts/validate-production.sh` with
`671 passed, 4 deselected` and `STAGE12_4_CONTAINER_VALIDATION=PASS` on Linux/amd64.

GitHub Actions `release-validation`: **NOT RUN** for this patch; the worktree changes are
uncommitted and unpushed, so no remote workflow result can be claimed.

External provider/Telegram smoke: `EXTERNAL_SMOKE = NOT_RUN` (no credentials supplied).

## Remaining known risks

Telegram remains at-least-once across external-send/SQLite commit windows. USER-mode reachability
is discovered at delivery time because Telegram provides no reliable preflight for arbitrary users;
the terminal notice is best effort. Provider and Telegram external smoke still require opt-in
credentials. Windows cannot provide Linux permission and FFmpeg evidence; CI/container gates remain
the authoritative Linux checks.
