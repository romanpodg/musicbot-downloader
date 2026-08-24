# Musicbot Downloader

Production-oriented foundation for a future Telegram music downloader service. This repository
implements Stage 0 through the Stage 12.4 deterministic production-validation baseline plus
Stages 13.1–13.5 provider account management and authorization: canonical recording identity, ambiguity-safe matching,
verified cross-provider discovery, runtime provider candidate resolution, quality-dependent
download planning, safe one-shot execution, persistent asynchronous queue orchestration, and
durable SingleFlight subscribers, a bot-scoped Telegram completed-result cache, and the
long-polling user downloader bot with persistent cached-file delivery, explicit Track Cards,
durable provider-specific Album Cards with selective track fan-out, and a centralized,
OWNER-hardened Telegram administration panel with owner-only administrator management and
ADMIN/OWNER runtime Download/Upload worker controls, live Provider Health diagnostics, and a
private authenticated Internal API with durable opaque Telegram deep links.

Current delivery roadmap:

- Stage 7: persistent download/upload queues;
- Stage 7.1: active-work SingleFlight and durable subscribers;
- Stage 8: Telegram completed-result cache;
- Stage 9: user Telegram bot runtime and durable user delivery;
- Stage 9.1: first/default quality selection;
- Stage 9.2: Track Card, explicit Download, and per-track quality choice;
- Stage 9.3: provider-specific album snapshots, durable track selection, and per-track delivery.
- Stage 10: Telegram Admin UI presentation foundation;
- Stage 10.1: centralized authorization and authoritative OWNER enforcement;
- Stage 10.2: owner-only promotion and removal of database-backed administrators;
- Stage 10.3: ADMIN/OWNER runtime Download and Upload worker controls.
- Stage 10.4: ADMIN/OWNER live Provider Health diagnostics.
- Stage 11: private Internal API and bot-scoped Track/Album deep-link registry.
- Stage 12.1: Production Packaging and Runtime Hardening Foundation.
- Stage 12.2: Crash Recovery and Stale Artifact Cleanup.
- Stage 12.3: Operational Audit and Recovery Tooling.
- Stage 12.4: Final Production Validation and Release Readiness (evidence-gated; see checklist).
- Stage 13.1: OWNER-only Provider Account Management Foundation.
- Stage 13.2: OWNER-only Tidal browser/device authorization with OnTheSpot-owned persistence and
  runtime verification.
- Stage 13.3: OWNER-only Deezer ARL authorization with immediate Telegram deletion, child-isolated
  HTTPS validation, OnTheSpot-owned persistence, and secure runtime verification.
- Stage 13.4: independent OWNER-only Spotify playback pairing and Developer Client Credentials for
  future catalog/search capability, both child-isolated and OnTheSpot-owned.
- Stage 13.5: unified startup reconciliation, atomic credential updates, provider reset, crash
  recovery, and sanitized readiness for Tidal, Deezer, and both Spotify components.
- Stage 14: Telegram Bot UX Foundation: localized UX messages, reusable menus, validated navigation
  callbacks, and transport-neutral interaction-state/progress foundations.
- Stage 15: Track Search Architecture: provider-neutral search models, provider contract, registry,
  application use case, and `/search` input foundation.
- Stage 16: Provider Search Integration: sequential Spotify, Deezer, and Tidal catalog search
  adapters over the existing isolated provider runtime, with provider-specific mapping into
  normalized search Tracks.

Stage 12.1 delivers the production packaging and runtime-hardening foundation. Stage 12.2 adds
deterministic startup crash recovery and conservative cleanup of stale Stage 6 artifacts. Stage
12.3 adds a bounded append-only operational audit, offline-safe inspection/recovery tooling,
validated online SQLite backup, and an OS-level one-runtime lock per SQLite database. Stage 12.4
adds repeatable credential-free Linux/container release validation; real Telegram/provider checks
remain explicitly opt-in. Stage 13.1 adds the provider-account-management architecture and
OWNER-only Telegram status UI. Stage 13.2 adds only Tidal device authorization; it does not
complete Stage 13. Stage 13.3 adds secure Deezer ARL authorization. Stage 13.4 adds independent
Spotify playback and Web API credential setup. Stage 13.5 completes the provider-account lifecycle
with atomic persistence, recovery, reset, and security hardening.

See [the production deployment guide](docs/production.md) for the container, filesystem,
migration, preflight, security, backup, restore, and upgrade contract. Stage 12.4 acceptance is
defined by [the release checklist](docs/release-checklist.md): it cannot be marked complete without
an actual Linux production-image build and executed container evidence.

See [the Stage 13.1 provider-account foundation](docs/stage13.1-provider-accounts.md) for its
authorization, state-model, coordinator, UI, credential-ownership, and explicit non-goal contract.
See [the Stage 13.2 Tidal authorization contract](docs/stage13.2-tidal-device-authorization.md) for
the child-isolated polling, OnTheSpot persistence, runtime verification, and lifecycle guarantees.
See [the Stage 13.3 Deezer authorization contract](docs/stage13.3-deezer-arl-authorization.md) for
the secret-message deletion gate, HTTPS-only child validation/login, credential ownership, and
leak-prevention guarantees.
See [the Stage 13.4 Spotify credential contract](docs/stage13.4-spotify-credentials.md) for bounded
local-network playback pairing, independent Client Credentials validation, discovery networking,
atomic OnTheSpot persistence, and token-cache invalidation.
See [the Stage 13.5 lifecycle contract](docs/stage13.5-lifecycle-hardening.md) for credential
ownership, startup reconciliation, crash recovery, atomic replacement, reset, readiness, filesystem,
and backup/restore guarantees.
See [the Stage 14 UX contract](docs/stage14-telegram-ux.md) for the Telegram presentation boundary,
message and keyboard foundations, callback format, navigation state, and compatibility guarantees.
See [the Stage 15 search architecture](docs/stage15-track-search.md) for the normalized domain
models, provider contract, registry, use case, and provider integration seam. See [the Stage 16
provider search integration](docs/stage16-provider-search-integration.md) for adapter isolation,
registry registration, response mapping, and availability handling.

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

An album is deliberately not a canonical cross-provider entity. Each accepted album URL creates a
provider-specific immutable snapshot containing its ordered track identities and display metadata.
Only selected tracks are resolved into canonical `Track` rows, lazily during expansion. Each child
then uses the normal provider-neutral TrackSource discovery, quality planning, SingleFlight, and
Telegram cache pipeline; the provider in the album URL is never forced as the download source.

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
The bot can still start without FFmpeg/ffprobe and serve valid Telegram cache hits. Cache misses
that require unavailable media tools fail through the existing typed Stage 6 error path.

The real downloader runtime additionally requires:

```env
BOT_TOKEN=<downloader bot token>
TELEGRAM_CACHE_CHAT_ID=<private cache chat/channel ID>
TELEGRAM_DELIVERY_WORKERS=4
TELEGRAM_DELIVERY_MAX_ATTEMPTS=3
TEMP_CLEANUP_INTERVAL_SECONDS=600
TEMP_ARTIFACT_STALE_AFTER_SECONDS=3600
```

Only the bot runtime requires the Telegram settings; Alembic and non-Telegram developer CLIs do
not. Delivery worker values are positive and bounded. Never commit the token.

OnTheSpot stores configuration in its platform config directory (normally
`%APPDATA%/onthespot/otsconfig.json` on Windows or `~/.config/onthespot/otsconfig.json` on Linux)
and logs/cache in the platform cache directory (normally `%TEMP%/onthespot` or
`~/.cache/onthespot`). If the cache directory is unavailable, upstream can fall back to `.logs/`;
that fallback and repository-local `otsconfig.json` are ignored by Git. An explicit
`ONTHESPOTDIR` inside this repository is rejected by the worker.

`OWNER_ID` is optional and must be positive when present. Startup promotes an existing matching
user to `OWNER`. It never creates a placeholder user; normal Telegram observation creates the real
profile and reconciles the configured owner. The application role model remains exactly `USER`,
`ADMIN`, and `OWNER`: administrators are database-managed, while the single owner identity is
anchored to `OWNER_ID` configuration.

OWNER authority requires both a persisted `OWNER` role and an exact stable numeric Telegram-ID
match with `OWNER_ID`; writing `OWNER` into the database alone never grants owner authority. A
stale `OWNER` row whose Telegram ID does not match the configured owner is treated as a privileged
role invariant violation and receives no admin-panel or owner-only permission. It is not
automatically guessed to be an ADMIN or USER, deleted, or demoted. Owner transfer and stale-row
demotion semantics remain outside Stage 10.2.

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

The filesystem and SQLite cannot share a transaction. Stage 12.2 runs durable recovery before
workers, protects artifacts referenced by every non-terminal UploadJob, and removes only recognized
UUID artifact roots that are unowned, inactive, and older than the configured stale threshold. A
Stage 6 crash before handoff therefore leaves a young orphan that is reclaimed later; a surviving
post-handoff UploadJob resumes with the same validated artifact. If ephemeral storage disappeared,
the UploadJob fails terminally and its waiting subscribers are reconciled. A terminal commit whose
release was interrupted becomes stale-cleanup eligible.

An exact ACTIVE Telegram cache row for the same bot, Track, and QualityProfile can rescue a pending
UploadJob after restart without a Telegram call; the UploadJob succeeds, waiting subscribers become
READY, and the local artifact is released. No fuzzy, wrong-quality, other-bot, or INVALID match is
accepted. Telegram accepting an upload before the cache row commits remains an unavoidable
at-least-once external side effect and can create an extra private-cache message on retry.

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

The real adapter uses async `aiogram` behind a small `TelegramGateway` (`getMe`, cached-file
`sendAudio`, cached-file `sendDocument`, and cache-chat uploads). `compose_stage8()` remains the
lower cache boundary. `compose_stage9()` adds the Dispatcher/router, Stage 7 queue manager, and a
fixed bounded pool of lease-based delivery workers. Long polling is the only update transport;
webhooks and inline mode are absent.
Setup and recovery details are in
[`docs/stage8-telegram-cache.md`](docs/stage8-telegram-cache.md).

## User Telegram bot (Stage 9–9.3)

The supported commands are `/start`, `/help`, `/quality`, and `/language`. The user sends a
supported track or album URL. Free-text search, playlists, podcasts, and group download flows are
not implemented. The downloader is intentionally private-chat-only for predictable privacy and
delivery semantics.

On the first real track request, the resolved canonical Track and request identity are persisted
as `AWAITING_QUALITY` before any download job is created. The four choices are exactly MP3 128,
MP3 320, AAC 256, and Lossless. The selection becomes `users.preferred_quality_profile`, and the
same persisted request continues automatically. `/quality` changes only future requests;
already-active requests retain their selected profile. `/language` sets `preferred_locale`, while
the Telegram language code continues to synchronize on interactions without overriding that
manual choice.

Users who already have a default quality now receive a single-track card before media preparation.
It shows canonical artist/title plus album and duration when available. The primary button snapshots
the user's default when the request is created (`Download · MP3 320`, for example); changing
`/quality` later does not reinterpret an older card. Merely sending a Track URL creates no download
job or SingleFlight subscriber—the bot waits for an explicit Download action.

`Other quality` opens exactly the same four QualityProfiles for this request only. Selecting one
immediately continues the request but never changes `users.preferred_quality_profile`. The
first-ever quality selection remains different: it is both the explicit action for that track and
the persisted global default, so the original request continues without another Download click.

Album URLs produce a durable Album Card without creating child download jobs. It shows the album
artist/title, track count, and available duration/release metadata. With an existing default,
`Download all`, `Select tracks`, and `Other quality` are offered. Selection is persisted in SQLite,
paginated eight tracks at a time, and supports per-track toggles, Select all, Clear all, Back, and
Download selected. The album quality is one snapshot shared by all selected child requests; the
one-off album picker does not change the user's default.

For a user's first-ever request, choosing a quality from an Album Card saves the global default and
returns to the card—the user must still explicitly choose Download all or Select tracks. Expansion
is restart-safe and idempotent: selected items become ordinary delivery requests, already-known
provider identities take the exact-source fast path, and unresolved identities pass through the
same conservative canonical matcher as standalone tracks. Duplicate album positions and concurrent
standalone/album requests converge through the existing `(track_id, quality)` SingleFlight key.

After either explicit action, the durable delivery worker still enters through
`DeliveryPreparationService`. An ACTIVE Telegram cache entry delivers its `file_id` without music
provider or FFmpeg work; a miss joins or creates the existing quality-scoped SingleFlight. Track
Card and one-off picker states are stored in SQLite and remain actionable after restart.

```text
Telegram track URL
  -> user/locale and canonical Track resolution
  -> durable telegram_delivery_requests row and quality snapshot
  -> Track Card / first default-quality picker
  -> explicit Download or quality selection
  -> Stage 8 cache-first preparation
     -> CACHE_HIT: queue cached file_id delivery
     -> PENDING: persist SingleFlight subscriber and wait durably
  -> subscriber READY resolves the ACTIVE cache row
  -> sendAudio(file_id) or sendDocument(file_id)
  -> DELIVERED and cache last_used_at

Telegram album URL
  -> provider-specific ordered album snapshot
  -> durable Album Card / first default-quality picker
  -> Download all or persisted paginated selection
  -> lazily resolve each selected item to a canonical Track
  -> create one ordinary delivery request per selected item
  -> existing cache-first / SingleFlight pipeline independently per track
  -> one localized terminal album summary
```

## Telegram administration (Stage 10-10.4)

`/admin` opens a compact read-only operational dashboard for a database `ADMIN` or the configured
authoritative `OWNER`. It is deliberately absent from the global command menu and works only in a
private chat with the same downloader bot. Ordinary users receive a localized access-denied
message, and group invocations never expose operational counts.

The dashboard shows local queue counts (queued/running/failed), worker desired/actual/default/max
values, active SingleFlight work and waiting subscribers, ACTIVE/INVALID Telegram cache counts,
waiting/sending/failed delivery counts, and active album-request count. `Refresh` re-authorizes the
interacting user, recomputes every local snapshot, and edits the existing panel message when
possible. `Close` re-authorizes before best-effort message deletion or keyboard removal. Neither
action changes queue, cache, worker, delivery, or album state.

`TelegramAuthorizationService` resolves each privileged request from current persistent user state
and immutable `OWNER_ID`; no in-memory admin-ID authority or persistent panel session exists. The
capability set is `ADMIN_PANEL_VIEW`, `WORKERS_MANAGE`, `PROVIDER_HEALTH_VIEW`, and `OWNER_ONLY`.
`AdminOverviewService`
requires a fresh `ADMIN_PANEL_VIEW` check before collecting bounded aggregates, so a forged
`adm1:refresh` callback is harmless and an open panel stops working immediately after an ADMIN is
demoted. A database `OWNER` role is not sufficient by itself: authoritative OWNER access also
requires that user's Telegram ID to match `OWNER_ID`. Mismatched stale OWNER rows receive no
privileged access and are not exposed as owners, administrators, or promotion candidates.

The authoritative OWNER sees `Administrators`, `Workers`, and `Provider Health` actions on
`/admin`; ordinary ADMIN users see `Workers` and `Provider Health` but never `Administrators`.
The owner-only management flow is:

```text
/admin -> Administrators -> Add administrator -> existing USER -> Confirm
Administrators -> existing ADMIN -> Remove administrator -> Confirm removal
```

`AdministratorManagementService` re-applies authoritative-owner authorization for every list,
detail, confirmation, and mutation. Administrator and eligible-user lists use SQL `COUNT` plus
bounded pages of eight rows ordered by internal user ID ascending. Promotion uses only existing
`USER` rows and performs an atomic conditional `USER -> ADMIN` transition; removal performs only
`ADMIN -> USER`. Duplicate or stale confirmations produce a safe stale result. OWNER assignment,
OWNER removal, and a generic role setter are not exposed by the Telegram UI. The configured owner
identity is also excluded by the conditional SQL predicates as defense in depth.

A user must interact with the downloader bot at least once before the OWNER can promote them to
ADMIN; the management UI never fabricates a Telegram profile or performs a global Contacts lookup.
Promotion grants the existing read-only `/admin` access immediately, and demotion revokes it on the
former ADMIN's next callback without a restart. Role changes do not alter locale, quality, profile,
download/album history, queue work, cache rows, or normal downloader access. The bot sends no
unsolicited promotion/demotion notification to the target user.

`RuntimeWorkerControlService` requires a fresh `WORKERS_MANAGE` permission for every worker read
and mutation. `ADMIN` and the authoritative `OWNER` receive this capability; `USER` and a stale,
mismatched `OWNER` do not. Administrator management remains separately guarded by `OWNER_ONLY`.
Consequently, promoting or demoting an administrator takes effect on their next open-panel or
button callback, including callbacks from an already-open Workers screen.

The worker UI is deliberately limited to the two Stage 7 pools:

```text
/admin -> Workers -> Download workers -> −1 / +1 / Reset to default
/admin -> Workers -> Upload workers   -> −1 / +1 / Reset to default
```

Each overview/detail shows four distinct values: **Desired** is the SQLite-persisted runtime
target, **Actual** is the number of current in-process worker tasks, **Default** is the ENV-backed
bootstrap/reset value, and **Maximum** is the ENV-backed hard safety ceiling. Desired and Actual
may temporarily differ while a pool converges. Refresh reads a fresh Stage 7 runtime snapshot.

Relative `+1`/`−1` operations use a short serialized SQLite update against the latest persisted
desired value, so concurrent administrators do not overwrite one another. Counts remain within
`1..*_WORKERS_MAX`; neither ADMIN nor OWNER can bypass the maximum. Reset immediately stores the
current process `*_WORKERS_DEFAULT`. Defaults and maxima remain deployment configuration and are
read-only in Telegram.

After a durable desired-state update, the existing `QueueManager` performs runtime resizing and
continuous reconciliation. Increasing Desired starts workers without restarting the application.
Decreasing Desired is graceful: workers already processing jobs finish their current work, do not
claim another job when marked for retirement, and then exit. Telegram handlers never directly
create or cancel asyncio worker tasks, and they do not wait for Actual to equal Desired.

Desired counts persist across application restarts. If an ENV maximum is later lowered below a
persisted value, Stage 7 startup reconciliation applies and persists the current safety ceiling.
The Workers UI does not edit queue capacity, retry/timeout settings, delivery/album worker counts,
or the shared OnTheSpot child-process count.

Dashboard and worker-control screens use only SQLite/runtime snapshots. They never authenticate or probe
music providers, validate Telegram cached files over the network, invoke FFmpeg, create downloads,
or mutate unrelated queue/cache/delivery/album state. Worker mutations change only persisted desired
Download/Upload counts. Compact `adm1`, `adm2`, and strict `adm3` callback data contains no desired
value, role, user ID, owner ID, token, provider identity, or Telegram `file_id`.

### Live Provider Health

`/admin -> Provider Health` performs an explicit live, read-only sweep of all eight
`MusicProviderName` values through the existing serialized OnTheSpot child. Opening the normal
dashboard, refreshing it, and opening Workers or Administrators remain network-free. Provider
Health is available to a current database `ADMIN` and the authoritative `OWNER`; every Open,
Refresh, and Back callback is authorized again, and `ProviderHealthService` independently enforces
`PROVIDER_HEALTH_VIEW` before any probe. A demoted administrator's old panel therefore cannot
trigger provider work, while a promoted administrator gains access immediately.

The screen shows the UTC check time and one normalized status per provider: `READY` means the
provider-level runtime/account path currently exposes enough state to attempt native acquisition;
`AUTH_REQUIRED` means required authentication or subscription state is absent; `UNAVAILABLE`
means a non-authenticated integration is not operational; `ERROR` means the check failed; and
`UNKNOWN` means pinned upstream state cannot safely prove readiness. In particular, pinned Qobuz
v1.8.1 only pings the service while loading saved credentials, so an active pool entry is reported
as `UNKNOWN`, not falsely promoted to `READY`.

Provider Health is an administrative diagnostic snapshot only. It is not used to make download
decisions. A `READY` result does not guarantee that a particular track exists, is regionally
playable, has a current stream URL, or satisfies a `QualityProfile`. Every real download performs
fresh provider/source validation through the existing Stage 4–6 pipeline. Likewise, a stale
non-ready snapshot cannot block a newly usable provider. The provider from which a user supplied a
Track URL does not have to be the provider used for acquisition.

Checks run only when an authorized administrator opens or refreshes Provider Health. There is no
background polling, durable health cache, health history, credential UI, login/logout flow, or
provider preference. Concurrent observations share only the currently running in-process sweep;
after it completes, the next observation is fresh. Provider failures are normalized independently,
with a 15-second bound per normalized provider inspection. Health is observational: it does not
reload sessions or mutate credentials. It performs no audio acquisition and never
invokes Stage 6, FFmpeg, or ffprobe. Telegram cache hits remain independent of provider health and
current music-provider authentication.

A local operator can inspect the same normalized service without Telegram authorization:

```bash
uv run python -m app.tools.provider_health
```

The CLI and Telegram view expose no usernames, account IDs, cookies, tokens, credentials, raw
account dictionaries, or raw upstream exception text. Callback data uses the strict `adm4`
namespace and carries only an action code.

Handlers do not wait for downloads. Delivery requests use a SQLite lease and bounded persistent
retry schedule, including Telegram `retry_after`. A confirmed invalid cached file reference
invalidates only that cache row and permits one persisted repair cycle. User-blocked, chat,
network, and rate-limit failures never invalidate shared media. Sending to Telegram and committing
SQLite cannot be atomic, so a process death after Telegram accepts a send but before the DELIVERED
commit retains the documented at-least-once duplicate edge case.

Apply migrations and run the single production entry point:

```bash
uv run alembic upgrade head
uv run python -m app.main --check
uv run python -m app.main
```

Startup verifies that the database is at Alembic head, reconciles OWNER, resolves the bot identity,
composes services, recovers expired queue/delivery/album leases and active artifact consistency,
reconciles SingleFlight and Album aggregates, runs one conservative stale-artifact sweep, starts
download/upload, delivery, and album workers, starts the supervised cleanup watchdog, optionally
starts the embedded Internal API, and begins polling. Recovery performs no provider work or
Telegram sends/uploads. Ctrl+C stops the
HTTP listener, polling, and fanout, gracefully stops queue workers, closes the OnTheSpot child and
Telegram session, and disposes database resources. `--check` validates and composes the HTTP
transport when enabled but never binds a socket.

Album expansion uses one bounded coordinator worker and a hard 500-track snapshot limit. Stage 11
adds the `deep_link_registry` table at Alembic head `20260820_0010`. It does not add API users,
publication rows, canonical Albums, media endpoints, or Provider Health coupling.

## Internal API and Telegram deep links (Stage 11)

The Internal API is intended only for localhost or a trusted private container/network path. Do
not expose it directly to the public internet. Stage 11 provides no TLS/reverse proxy and no
public audio download or streaming endpoint. Full deployment hardening belongs to Stage 12.

The API is disabled by default. Enable it with a dedicated high-entropy secret unrelated to
`BOT_TOKEN`, provider credentials, and Telegram ADMIN/OWNER authorization:

```env
INTERNAL_API_ENABLED=true
INTERNAL_API_HOST=127.0.0.1
INTERNAL_API_PORT=8081
INTERNAL_API_TOKEN=<at-least-32-character-high-entropy-secret>
```

Generate a value with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
Authentication is `Authorization: Bearer <INTERNAL_API_TOKEN>` only; query-string credentials are
not accepted. The liveness-only `GET /internal/v1/health` is deliberately unauthenticated and
returns only `{"status":"ok"}`. All registry endpoints require the Bearer secret:

```bash
curl -X POST http://127.0.0.1:8081/internal/v1/deep-links \
  -H "Authorization: Bearer $INTERNAL_API_TOKEN" \
  -H "Idempotency-Key: publishing-item-123" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://open.spotify.com/track/..."}'
curl -H "Authorization: Bearer $INTERNAL_API_TOKEN" \
  http://127.0.0.1:8081/internal/v1/deep-links/d1_...
curl -X POST -H "Authorization: Bearer $INTERNAL_API_TOKEN" \
  http://127.0.0.1:8081/internal/v1/deep-links/d1_.../revoke
```

Registration accepts supported `TRACK` and `ALBUM` URLs only. Track URLs run the existing Stage 3
canonical resolver and verified source discovery, then store only the canonical Track ID. Album
URLs store the safe provider-release identity `(provider, provider_album_id)` because the project
deliberately has no unsafe cross-provider canonical Album. Registration creates no Telegram user
request, queue job, cache entry, artifact, or download. Provider Health is not consulted.

The returned `d1_` parameter contains 192 random bits encoded with unpadded base64url. It is a
reusable public capability, not an API credential, and is scoped by the numeric downloader-bot
identity from Telegram `getMe`. Rotating `BOT_TOKEN` for the same bot preserves links; a different
bot ID cannot resolve them. The current bot username constructs each response URL but is not
persisted.

`Idempotency-Key` is optional and bounded to 128 characters, but publishing integrations should
send a stable publication-item identifier. The registry stores a SHA-256 fingerprint of
`target type + canonical provider value + provider item ID`, never the raw request URL. Replaying
the same bot/key/fingerprint returns the original token with `created: false`; using the key for a
different target returns `409 IDEMPOTENCY_KEY_CONFLICT`. Different keys may intentionally create
independent links for the same Track.

Opening an ACTIVE Track link loads the canonical Track locally and creates the normal durable
Stage 9.2 Track Card. Opening an ACTIVE Album link resolves its stored provider release and creates
the normal per-user Stage 9.3 Album snapshot/Card. Neither action starts media work. First-quality,
explicit Download/Download All/Select Tracks, Telegram cache-first delivery, and SingleFlight are
unchanged. Revocation only blocks future token resolution; already materialized cards, Album
requests, cache entries, deliveries, and queue work remain intact. Album links depend on their
provider-release metadata remaining resolvable when opened.

The complete transport contract, stable errors, security decisions, and examples are documented
in [`docs/stage11-internal-api.md`](docs/stage11-internal-api.md).

Authentication with only a subset of supported music providers is valid. Download planning uses
only providers available at actual execution time; it never requires the provider from the input
URL to perform the download. Provider availability is re-evaluated during execution. Telegram
cache hits require no current music-provider authentication, and no background provider-login
polling is performed.

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

Worker changes use the same persisted service as the Telegram Workers UI. The inspection CLI
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

## Local operational tooling

Stage 12.3 provides one network-free operator entry point:

```bash
uv run python -m app.tools.ops status
uv run python -m app.tools.ops status --json
uv run python -m app.tools.ops audit list --limit 50 --json
uv run python -m app.tools.ops recovery inspect
uv run python -m app.tools.ops recovery run
uv run python -m app.tools.ops artifacts scan
uv run python -m app.tools.ops artifacts cleanup
uv run python -m app.tools.ops backup create /protected/backups/musicbot.db
```

`status` and `audit list` can run beside the application. Status reports persisted desired worker
counts and explicitly marks in-process actual counts unavailable. Recovery and artifact commands
acquire the same exclusive instance lock as the runtime and refuse while it is live; artifact scan
is also offline because an external process cannot see the active-artifact registry. Online backup
uses SQLite snapshot semantics and can run while the application holds the runtime lock.

The persistent audit records only successful high-value role, worker-setting, Deep-Link, recovery,
cleanup, and backup transitions. It is not a full activity log: user downloads, cache hits, denied
attacker-controlled traffic, provider credentials, raw URLs/tokens, and Telegram updates are not
stored. Audit metadata is typed, allow-listed, deterministically serialized, and bounded. No audit
retention scheduler exists yet.

One active downloader application instance per SQLite database is technically enforced by an
OS advisory lock beside the database (`<database>.instance.lock`). This is single-host process
coordination, not a distributed lock or multi-replica design.

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
- Stale artifact cleanup is deliberately delayed by a conservative threshold; it is not an exact
  byte-reservation system and ignores unknown TEMP_DIR entries.
- The runtime enforces one active application instance per SQLite database on one host; multi-host
  workers and distributed locks are intentionally absent.
- Telegram upload and cache persistence are not one distributed transaction. A process crash after
  Telegram accepts a file but before SQLite commits can leave an extra cache-chat message on retry.
- Telegram user delivery is at-least-once across the external-send/SQLite-commit crash window.
- Album snapshots are provider-specific and are not merged across services. Album metadata is
  derived from the pinned provider's ordered track listing and can be incomplete.
- Album delivery is per track. There is no ZIP/archive, concatenated audio, M3U, album-wide media
  job/cache artifact, strict Telegram delivery ordering, or per-track quality override.
- Stage 10.2 administrator management is owner-only. OWNER transfer is not implemented. The
  operational audit intentionally excludes ordinary user activity and has no automated retention.
  Stage 10.3 controls only Download/Upload desired counts;
  delivery/album/OnTheSpot worker counts and ENV defaults/maxima remain outside this UI.
- Provider Health cannot guarantee TrackSource playability or quality. Some pinned integrations,
  notably Qobuz, expose no safe provider-level authorization validation, so readiness remains
  `UNKNOWN`. Checks use the serialized OnTheSpot worker; there is no history, background polling,
  credential management, or provider enable/ranking UI.
- Changing `OWNER_ID` does not automatically demote stale OWNER rows; mismatched rows are denied all
  privileged access until a future explicit owner-transfer/revocation policy is implemented.
- Playlists, webhooks, inline mode, the publishing bot itself, channel publication, deep-link
  analytics/expiration, API rate limiting, API users/JWT, user management, and cache eviction are
  absent. The API has one deployment-owned static secret and remains a localhost/private-network
  interface. SQLite and one application instance remain authoritative. YouTube Music album URLs
  are unsupported by the pinned adapter.
