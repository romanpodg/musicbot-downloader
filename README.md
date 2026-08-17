# Musicbot Downloader

Production-oriented foundation for a future Telegram music downloader service. This repository
implements Stage 0 through Stage 8: canonical recording identity, ambiguity-safe matching,
verified cross-provider discovery, runtime provider candidate resolution, quality-dependent
download planning, safe one-shot execution, persistent asynchronous queue orchestration, and
durable SingleFlight subscribers, and a bot-scoped Telegram completed-result cache. It does not
contain the Stage 9 user-facing Telegram bot, handlers, polling/webhooks, or an API.

Current delivery roadmap:

- Stage 7: persistent download/upload queues;
- Stage 7.1: active-work SingleFlight and durable subscribers;
- Stage 8: Telegram completed-result cache;
- Stage 9: user Telegram bot — not implemented yet.

## Architecture

The central identity rule is:

```text
Track identity != search provider != download provider
```

`Track` means one specific audio recording/version, independent of streaming provider. It is not
a provider catalog item, composition, album slot, or URL. Provider identities and canonical URLs
live in `track_sources`, so a confirmed recording can have several provider sources. Album
difference is weak evidence and does not prevent a match; Live, Remix, Remaster, Acoustic,
Explicit/Clean, and similar recording variants remain separate when metadata shows a conflict.

Stage 3 resolves an exact `(provider, provider_track_id)` first, then performs bounded indexed
database candidate lookup. Valid matching ISRC is strong but not absolute evidence. Without ISRC,
automatic matching requires normalized title and artist equality plus duration within 3000 ms.
A 3001–5000 ms difference is plausible but ambiguous, and more than 5000 ms is a conflict.
Missing duration never counts as a match. Multiple compatible candidates return `AMBIGUOUS`; an
uncertain input source receives its own Track instead of being attached to an existing candidate.

The four user delivery profiles are exactly `MP3_128`, `MP3_320`, `AAC_256`, and `LOSSLESS`.
Provider-native codec/container/bitrate data is a separate nullable model.

Application services consume the local `MusicProvider` abstraction. The main process never
imports OnTheSpot or librespot. The OnTheSpot adapter communicates over size-limited JSON Lines
with one lazy, long-lived child interpreter launched as:

```text
python -m app.providers.onthespot.worker
```

The child owns OnTheSpot's global accounts/configuration, logging handlers, exception hook, and
Protobuf compatibility setting. Current metadata, search, provider source-check, preflight, and
native-download requests are serialized by the process client.
This is a dependency-isolation boundary, not a download worker pool.

## Internationalization

Locale catalogs are JSON files under `app/i18n/locales/<locale>/`. Startup validates catalog
shape and key parity. Locale selection is independent of Telegram and follows:

1. supported explicit user preference;
2. supported Telegram language code, including normalized forms such as `ru_RU` and `ru-RU`;
3. configured default locale.

Translation lookup falls back to the default catalog and then returns the key deterministically.
Interpolation failures use a typed localization error. Business services and domain exceptions
do not contain translated presentation text.

## Requirements and setup

- CPython 3.12 (`>=3.12,<3.13`)
- `uv`
- SQLite (included with Python)
- Git for the pinned optional OnTheSpot dependency
- `ffprobe` for media validation; `ffmpeg` for transcode plans and optional stream-copy tags

```bash
cp .env.example .env
uv sync --locked --extra dev
```

For OnTheSpot metadata resolution, install the full locked optional dependency tree:

```bash
uv sync --locked --extra dev --extra onthespot
```

OnTheSpot is pinned to v1.8.1 commit `8ed6cf33ef772e6569d5014237e0fb4ce8b9e45d`.
It remains optional because its package installs GUI and media dependencies even for metadata-only
use. Configure accounts using OnTheSpot's own supported configuration. The child process forces
`PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python` before importing Protobuf/OnTheSpot and gives
upstream the numeric `LOG_LEVEL=20`; neither value is changed in the application process.

`FFMPEG_BINARY` and `FFPROBE_BINARY` may name explicit cross-platform binaries. Blank values use
normal PATH discovery. `DOWNLOAD_TIMEOUT_SECONDS` and `TRANSCODE_TIMEOUT_SECONDS` must be positive.

OnTheSpot stores configuration in its platform config directory (normally
`%APPDATA%/onthespot/otsconfig.json` on Windows or `~/.config/onthespot/otsconfig.json` on Linux)
and logs/cache in the platform cache directory (normally `%TEMP%/onthespot` or
`~/.cache/onthespot`). If the cache directory is unavailable, upstream can fall back to `.logs/`;
that fallback and repository-local `otsconfig.json` are ignored by Git. An explicit
`ONTHESPOTDIR` inside this repository is rejected by the worker.

`OWNER_ID` is optional and must be positive when present. Startup promotes an existing matching
user to `OWNER`. It never creates a placeholder user; the future Telegram first-contact flow must
create the real profile and rerun owner reconciliation. Changing `OWNER_ID` does not currently
demote a stale OWNER row because owner transfer/revocation policy belongs to a later authorization
stage.

## Database migrations

Production schema changes use Alembic, not `Base.metadata.create_all()`:

```bash
uv run alembic upgrade head
uv run alembic check
```

SQLite initialization centrally enables WAL, foreign keys, and a 5000 ms busy timeout. Provider
enum values are persisted as stable lowercase values. ISRC is validated, nullable, indexed, and
non-unique. Internal normalized artist/title keys have a composite index and are maintained during
conservative canonical metadata enrichment; they are candidate keys, not public identity.

For an existing source, resolution fills only missing Track fields. Existing canonical values and a
known ISRC are not overwritten by conflicting or degraded responses. Provider metadata uses an
allowlisted, non-destructive merge and never stores cookies, tokens, or credentials. A provider
identity cannot move to another Track through ordinary upsert. If verified discovery finds a
source owned by another Track, it reports `IDENTITY_CONFLICT` and does not merge or reassign it.

Cross-provider search is bounded to 10 lightweight candidates per initialized provider. Every
candidate is resolved to full metadata and passed through the same matcher before persistence;
search result text alone is never trusted. Provider failures are reported independently and do not
roll back successful canonical input resolution. Searchability is identity discovery only—it does
not choose a future download provider.

Stage 4 starts only from the canonical Track's persisted, verified `TrackSource` rows. It does not
perform fresh fuzzy discovery. `ProviderResolver` reports each source's separate metadata, search,
download, authentication, runtime, and known native-media capabilities. Runtime states are
`AVAILABLE`, `AUTH_REQUIRED`, `UNAVAILABLE`, `UNSUPPORTED`, `SOURCE_UNAVAILABLE`, and `ERROR`.
One provider failure remains local to that source, and no usable sources produce the structured
`NO_AVAILABLE_PROVIDER` outcome.

Stage 5 accepts only a canonical `track_id` and one of the four `QualityProfile` values. Its
`QualityResolver` asks `ProviderResolver` for a new runtime snapshot on every call, filters out
every status except `AVAILABLE`, and returns deterministic ordered `DownloadPlan` strategies.
Each plan carries stable provider/source identity, a source-media requirement, the requested
output contract, an operation, readiness, and a machine-readable reason. The first plan is the
primary strategy and the remaining plans are fallbacks; Stage 5 does not execute any of them.

The quality policy is deliberately strict:

- a confirmed native codec and nominal bitrate exact match is preferred;
- genuine lossless to requested MP3 or AAC is an allowed future transcode;
- lossy-to-lossy transcoding is forbidden, including down-conversion;
- lossy-to-lossless conversion and bitrate upscaling are forbidden;
- `LOSSLESS` preserves a genuine lossless source instead of re-encoding it;
- unknown media never becomes a confirmed plan.

When a normalized provider capability describes a bounded future path but the exact stream is not
known yet, the plan is marked `REQUIRES_PREFLIGHT` and includes the facts Stage 6 must verify.
For example, Tidal may conditionally require a genuine lossless stream; Deezer may expose separate
conditional native-MP3 and lossless-transcode strategies. No manifest or media is fetched in
Stage 5.

Provider availability is evaluated at request time from current authentication/session state. A
provider that is supported but not currently authenticated is not eligible for download planning.
Stage 6 revalidates every selected provider immediately before execution.

The flow is deliberately split:

```text
Track
  -> verified TrackSources (Stage 3)
  -> Provider Resolver candidates (Stage 4)
  -> Quality Resolver plans (Stage 5)
  -> revalidated native acquisition and validated temporary artifact (Stage 6)
  -> persistent UploadJob handoff and injected delivery executor (Stage 7)
  -> shared active flight and persistent caller outcomes (Stage 7.1)
  -> durable Telegram file_id cache and cache-first delivery preparation (Stage 8)
```

In operational terms, Stage 3 answers “what is this recording?”, Stage 4 answers “which verified
providers are currently usable?”, Stage 5 answers “which source/transformation safely satisfies
the requested quality?”, and Stage 6 revalidates and executes that ordered plan set. Provider
authorization is checked dynamically. Stage 6 revalidates provider/source availability
immediately before every plan attempt, skips newly unauthenticated sources, and preserves Stage 5
fallback order.

Stage 6 calls only OnTheSpot's pinned provider-native download dispatcher in the isolated child.
It bypasses the upstream finalizer that converts to `track_file_format`, applies `file_bitrate`, and
tags media. Provider-native transport decryption remains allowed: Apple Music uses FFmpeg stream
copy with its provider decryption key and Deezer uses its native Blowfish decryptor. Both yield the
native encoded stream; neither is application-quality transcoding.

The execution policy is intentionally narrow:

- native exact media is delivered directly after probing;
- genuine provider-native FLAC may be transcoded to requested MP3 128, MP3 320, or AAC 256;
- lossy-to-lossy, lossy-to-lossless, and bitrate upscaling are never executed;
- genuine lossless is preserved directly rather than re-encoded.

Every request owns `TEMP_DIR/<uuid>/`, with isolated attempt source directories and one output
directory. Rejected attempts are removed before fallback. A successful `DownloadResult` retains
only its validated final artifact until `DownloadArtifactManager.release(job_id)` is called;
release is containment-checked and idempotent. Audio is stored only temporarily. Stage 6 does not
introduce a permanent local music cache.

## Persistent queues and workers

Stage 6 is the one-shot execution boundary. Stage 7 persists `DownloadJob` and `UploadJob` rows in
the same SQLite/WAL database and uses that database—not an in-memory queue—as the source of truth:

```text
DownloadJob (FIFO claim)
  -> download worker
  -> fresh DownloadPipeline.download(track_id, quality_profile)
  -> validated Stage 6 artifact
  -> atomic DownloadJob SUCCEEDED + unique UploadJob insert
  -> upload worker
  -> injected UploadExecutor
  -> UploadJob SUCCEEDED
  -> artifact release
```

Jobs use `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, and `CANCELLED`. A claim is one atomic
SQLite `UPDATE ... RETURNING` over the first eligible row ordered by `queued_at, id`. Claims set an
in-process worker identity and conservative lease; heartbeats protect long work, while an expired
lease is requeued or terminalized according to its bounded attempt count. This is minimum ordinary
restart safety for one application instance with multiple async workers, not a distributed or
exactly-once queue.

Queue retries use persisted `available_at` exponential backoff. Every DownloadJob retry calls Stage
6 from scratch, so provider planning, authentication, and runtime state are current. Stage 6 provider
fallback is separate from a Stage 7 retry. The pinned OnTheSpot child still serializes native
provider operations; DB work, planning, transcoding, future backends, and uploads can overlap.

Artifact ownership is explicit. Stage 6 initially owns a successful `DownloadResult`; the download
worker validates containment and stores a TEMP_DIR-relative artifact path in the unique UploadJob
handoff transaction. A failed handoff releases the artifact. Retryable uploads retain it. Successful,
terminal, or cancelled uploads commit their status and then release only through
`DownloadArtifactManager`. DB paths are checked under `TEMP_DIR/<job_id>/output` before use.

The filesystem and SQLite cannot share a transaction. A crash after Stage 6 returns but before
handoff can leave an orphan directory; a crash after external delivery but before the UploadJob
success commit can cause duplicate delivery; and a crash after a terminal commit but before release
can leak an artifact. Full cleanup/reconstruction remains deferred to the later recovery stage.

Worker settings have three distinct authorities:

- `DOWNLOAD_WORKERS_DEFAULT` / `UPLOAD_WORKERS_DEFAULT` bootstrap a missing DB singleton.
- `DOWNLOAD_WORKERS_MAX` / `UPLOAD_WORKERS_MAX` are hard current-process safety ceilings.
- `runtime_settings` stores current desired values changed at runtime.

Explicit runtime values outside `1..ENV_MAX` are rejected. On startup only, a stored value above a
newly lowered maximum is clamped and persisted. `WorkerSettingsService` changes desired values
without restart; `QueueManager` immediately resizes independent pools and continuously reconciles
actual workers to desired state. Upscaling starts tasks. Downscaling lets excess workers finish their
active job and retire without claiming another.

`QUEUE_MAX_SIZE` limits active non-terminal DownloadJobs accepted through submission. Terminal
DownloadJobs do not count, and internally created UploadJobs do not use this limit. Job history is
retained and inspection is bounded and paginated.

Stage 7.1 makes `SingleFlightService.submit()` the normal caller admission path. Its identity is
exactly `(track_id, QualityProfile)`; provider, source URL, requester, authentication state, and
the runtime DownloadPlan are deliberately excluded. A short SQLite `BEGIN IMMEDIATE` transaction
reconciles an existing row, joins it when eligible, or atomically creates one DownloadJob, one
`download_flights` row, and one UUID `job_subscribers` row. The unique flight key prevents
concurrent first-request races across asyncio tasks. The raw `DownloadQueueService.submit()` method
remains an internal maintenance/test primitive and does not deduplicate.

```text
N requests for Track + QualityProfile
  -> 1 active download_flights row
  -> 1 DownloadJob
  -> N durable WAITING subscribers
  -> 1 Stage 6 execution stream
  -> at most 1 UploadJob
  -> upload terminal outcome
  -> subscribers READY / FAILED / CANCELLED
  -> flight row removed
```

`READY` means the shared UploadJob succeeded; it does not mean a Telegram user received a file.
Only `WAITING` subscribers receive shared terminal propagation, so an explicitly cancelled
subscriber stays `CANCELLED`. Cancelling one subscriber leaves shared work running while any other
subscriber waits. Cancelling the last waiter closes admission to that flight and requests
best-effort cooperative cancellation of the queued/running DownloadJob or UploadJob. Administrative
shared-job cancellation terminalizes all remaining waiters.

Flight rows represent only currently active work. Completion removes the coordination row while
DownloadJob, UploadJob, and subscriber history remain. Submission-time and manager/worker
reconciliation repair crash windows such as a terminal UploadJob committed before subscriber
propagation. In-process conditions wake local waiters promptly, with bounded SQLite reloads as the
restart-safe source of truth. SingleFlight still represents active work only; Stage 8 owns completed
reuse.

## Telegram completed-result cache

`DeliveryPreparationService` is the future Stage 9 caller boundary. In one short
`BEGIN IMMEDIATE` transaction it checks the current bot's ACTIVE cache row, then joins or creates
the existing SingleFlight work only on a miss. The completed-cache identity is exactly:

```text
telegram_bot_id + track_id + QualityProfile
```

Telegram `file_id` values belong to the bot that obtained them. The numeric bot ID is resolved with
Telegram `getMe`; it is never derived from or hashed from `BOT_TOKEN`. Token rotation for the same
bot keeps the namespace, while switching bots cannot accidentally reuse incompatible file IDs.

```text
Active duplicate requests    -> Stage 7.1 SingleFlight
Completed duplicate requests -> Stage 8 Telegram file_id cache
```

`TelegramCacheUploadExecutor` implements the generic Stage 7 `UploadExecutor`. It validates the
durable Stage 6 facts carried by the UploadJob, skips the network when the exact cache key is already
ACTIVE, uploads once to the private cache chat, and persists the normalized receipt before returning
success. Only then can the generic worker mark the UploadJob `SUCCEEDED`, move subscribers to
`READY`, close the flight, and release the local artifact. Thus `READY` implies an ACTIVE cache row,
and later cache hits need no local audio.

MP3 and M4A/AAC use Telegram AUDIO transport; FLAC/lossless and other containers use DOCUMENT.
Telegram never causes a transcode or quality downgrade. Cache rows retain actual output media facts,
the successful fallback provider/source, DIRECT/TRANSCODE operation, and encoder provenance. An
optional TrackSource FK becomes NULL if the live source disappears, while denormalized provider
provenance and the cached file remain valid.

ACTIVE rows can be explicitly changed to INVALID. The next request then uses normal SingleFlight;
a successful replacement upload reactivates the same logical row. There is no eviction, refresh
scheduler, negative provider cache, or periodic Telegram validation. A valid hit bypasses music
provider authentication, provider resolution, downloads, FFmpeg, queues, and Telegram network calls.

The real adapter uses async `aiogram` behind a small `TelegramGateway` (`getMe`, `sendAudio`, and
`sendDocument`). No Dispatcher, long polling, webhook, commands, callbacks, or user delivery fanout
is started. `compose_stage8()` builds the gateway, cache service, production upload executor, and
delivery service for an explicit queue/application lifecycle; `app.main` still starts no daemon.
Setup and recovery details are in
[`docs/stage8-telegram-cache.md`](docs/stage8-telegram-cache.md).

Source and output files are probed with direct argv-based `ffprobe` execution. Codec, container,
bitrate, sample rate, bit depth, channels, audio-stream presence, and duration are normalized.
Known canonical duration uses a 3000 ms tolerance. Transcoding uses direct argv-based FFmpeg:
`libmp3lame -b:a 128k`, `libmp3lame -b:a 320k`, or the built-in `aac -b:a 256k` in an M4A/IPOD
muxer. Output bitrate validation allows 20 percent for encoder/muxer reporting. Canonical title,
artist, album, and ISRC are added during transcode; direct files use best-effort stream-copy tagging
without audio re-encoding and otherwise retain provider-native tags.

Stage 5 ranks confirmed native exact delivery, preflight native exact delivery, confirmed
lossless-to-lossy transcoding, then preflight lossless-to-lossy transcoding. For `LOSSLESS`, only
confirmed then preflight genuine-lossless preservation qualifies. Equal strategies use stable
provider value, provider track identity, and TrackSource identity as neutral tie-breakers. The
provider from the original input URL receives no special status. The pinned implementation matrix
is documented in
[`docs/provider-capability-matrix.md`](docs/provider-capability-matrix.md).

## Development checks

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy app
uv run pytest -m "not external"
uv lock --check
```

External provider tests are opt-in:

```bash
ONTHESPOT_TEST_TRACK_URL="<TRACK_URL>" uv run pytest -m external
```

They do not require private credentials by default; providers that need authentication report a
typed provider error when no configured account is available.

The real Telegram gateway test is also opt-in and leaves one small synthetic test document in the
explicitly configured test chat:

```bash
BOT_TOKEN="..." TELEGRAM_TEST_CACHE_CHAT_ID="..." uv run pytest -m external \
  tests/integration/test_telegram_external.py
```

## Resolve tool

After applying migrations and configuring OnTheSpot if needed:

```bash
uv run python -m app.tools.resolve "<TRACK_URL>"
```

The command validates and canonicalizes one supported track URL, retrieves normalized metadata,
and resolves it to a canonical Track. Repeating the same exact identity is idempotent. To search
other initialized provider catalogs and attach only fully verified matches:

```bash
uv run python -m app.tools.resolve "<TRACK_URL>" --discover
```

OnTheSpot v1.8.1 exposes common lightweight track search results for Apple Music, Bandcamp,
Deezer, Qobuz, SoundCloud, Spotify, Tidal, and YouTube Music. Bandcamp and YouTube Music are
tokenless; the other providers require an active OnTheSpot account/session. Upstream has no
dedicated cross-provider ISRC-search contract, so Stage 3 uses the recording's artist and full
title (including version text) as its bounded query and verifies full metadata afterward.

## Provider candidate tool

After Stage 3 has persisted a canonical Track and its verified sources, inspect Stage 4 runtime
candidates with:

```bash
uv run python -m app.tools.providers <TRACK_ID>
```

The command reports usable candidates and normalized failures. It does not select a provider for
`MP3_128`, `MP3_320`, `AAC_256`, or `LOSSLESS`, and it does not download audio.

## Quality planning tool

Obtain a fresh Stage 4 snapshot and inspect Stage 5 primary and fallback plans with:

```bash
uv run python -m app.tools.quality <TRACK_ID> MP3_128
uv run python -m app.tools.quality <TRACK_ID> MP3_320
uv run python -m app.tools.quality <TRACK_ID> AAC_256
uv run python -m app.tools.quality <TRACK_ID> LOSSLESS
```

The command shows current provider statuses, ordered plans, preflight requirements, and typed
rejections. It performs no download, manifest fetch, transcoding, or FFmpeg invocation.

## Download tool

Execute fresh Stage 5 planning and the Stage 6 pipeline for any supported profile:

```bash
uv run python -m app.tools.download <TRACK_ID> MP3_128
uv run python -m app.tools.download <TRACK_ID> MP3_320
uv run python -m app.tools.download <TRACK_ID> AAC_256
uv run python -m app.tools.download <TRACK_ID> LOSSLESS
```

Without `--output`, the command prints the structured result and releases all temporary audio
before exit. To copy the validated artifact to an explicit developer-owned destination first:

```bash
uv run python -m app.tools.download <TRACK_ID> MP3_320 --output ./verified-track.mp3
```

The destination parent must exist and an existing file is never overwritten. Account-backed
providers use already configured OnTheSpot accounts; credentials are neither accepted by this CLI
nor printed.

## Queue tool

Apply migrations first. Queue inspection never starts workers or pretends to upload:

```bash
uv run python -m app.tools.queue submit <TRACK_ID> MP3_320
uv run python -m app.tools.queue submit <TRACK_ID> MP3_320 --request-key test-123
uv run python -m app.tools.queue subscriber <SUBSCRIBER_ID>
uv run python -m app.tools.queue subscribers <DOWNLOAD_JOB_ID> --limit 50
uv run python -m app.tools.queue cancel-subscriber <SUBSCRIBER_ID>
uv run python -m app.tools.queue wait <SUBSCRIBER_ID> --timeout 30
uv run python -m app.tools.queue status
uv run python -m app.tools.queue jobs download --limit 50
uv run python -m app.tools.queue jobs upload --limit 50
uv run python -m app.tools.queue workers
uv run python -m app.tools.queue workers download 4
uv run python -m app.tools.queue workers upload 5
```

Worker changes use the same persisted service intended for the future admin UI. The inspection CLI
has no running pool and reports zero actual workers; an executor-enabled QueueManager immediately
reconciles to the stored desired values.

## Telegram cache tools

Metadata-only cache inspection does not require Telegram credentials:

```bash
uv run python -m app.tools.telegram_cache status
uv run python -m app.tools.telegram_cache list --limit 50
uv run python -m app.tools.telegram_cache get <TRACK_ID> MP3_320 --bot-id <BOT_ID>
uv run python -m app.tools.telegram_cache invalidate <CACHE_ID> --reason-code MANUAL_INVALIDATION
```

Resolve the configured bot identity with `getMe`, or exercise cache-first admission, with:

```bash
uv run python -m app.tools.telegram_cache verify-gateway
uv run python -m app.tools.delivery prepare <TRACK_ID> MP3_320 --request-key manual-check
```

The CLIs redact `file_id` by default and never print `BOT_TOKEN`. `get` may resolve the current bot
when `--bot-id` is omitted, which requires Telegram configuration. Cache status/list/invalidate are
SQLite-only operations.

## Current limitations

- OnTheSpot exposes no documented stable Python library API. Only the child worker imports the
  inspected v1.8.1 account loader, URL matcher, metadata registry, and token selector.
- OnTheSpot's required pure-Python Protobuf mode can reduce Protobuf performance inside the child;
  it does not affect the application process.
- The worker deliberately does not auto-respawn after a crash. Its owner must close and replace
  the failed process client during controlled application lifecycle recovery.
- Exact native facts remain unknown before download for provider paths that expose no reliable
  manifest/selection preflight; Stage 6 downloads then probes and rejects mismatches.
- Stage 4 source checks use the pinned provider metadata functions. They cannot guarantee that a
  later expiring or region-dependent stream URL will remain available.
- Search is serialized through one OnTheSpot worker and can be slow across several providers.
- Incomplete metadata intentionally produces ambiguity or no match rather than fuzzy guessing.
- `ffprobe` is the current mandatory application media-validation implementation. Direct delivery
  cannot succeed safely when it is unavailable. FFmpeg absence affects transcode plans and optional
  direct stream-copy tagging; a later direct fallback can still succeed when ffprobe is available.
- There is no automatic stale-artifact watchdog yet; the immediate caller owns successful release.
- Stage 7 targets one application instance and one SQLite database; multi-host workers and
  distributed locks are intentionally absent.
- Telegram upload and cache persistence are not one distributed transaction. A process crash after
  Telegram accepts a file but before SQLite commits can leave an extra cache-chat message on retry.
- Stage 8 provides the production upload executor and cache-first services, but no long-running
  queue daemon is started by `app.main`; Stage 9 will own the user-bot lifecycle.
- User Telegram delivery, handlers, polling/webhooks, bot/admin UI, albums, playlists, cache
  refresh/eviction, and APIs are intentionally absent.
