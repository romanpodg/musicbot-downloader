# Musicbot Downloader

Production-oriented foundation for a future Telegram music downloader service. This repository
implements Stage 0 through Stage 6: canonical recording identity, ambiguity-safe matching,
verified cross-provider discovery, runtime provider candidate resolution, and quality-dependent
download planning plus safe one-shot execution. It does not contain a Telegram bot, queue, worker
pool, or API.

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
- Albums, playlists, Telegram upload/cache, queues, and APIs are intentionally absent.
