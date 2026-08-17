# Musicbot Downloader

Production-oriented foundation for a future Telegram music downloader service. This repository
implements Stage 0, Stage 1, and the metadata-only portion of Stage 2. It does not contain a
Telegram bot, cross-provider resolver, quality resolver, downloader, queue, worker pool, or API.

## Architecture

The central identity rule is:

```text
Track identity != search provider != download provider
```

`tracks` stores nullable provider-independent metadata. Provider identities and canonical URLs
live in `track_sources`, and one track may eventually have several sources. Stage 2 deliberately
does not attach a new source to a Track by ISRC: an unseen `(provider, provider_track_id)` creates
a new Track. Cross-provider identity decisions belong exclusively to Stage 3.

The four user delivery profiles are exactly `MP3_128`, `MP3_320`, `AAC_256`, and `LOSSLESS`.
Provider-native codec/container/bitrate data is a separate nullable model.

Application services consume the local `MusicProvider` abstraction. The main process never
imports OnTheSpot or librespot. The OnTheSpot adapter communicates over size-limited JSON Lines
with one lazy, long-lived child interpreter launched as:

```text
python -m app.providers.onthespot.worker
```

The child owns OnTheSpot's global accounts/configuration, logging handlers, exception hook, and
Protobuf compatibility setting. Current metadata requests are serialized by the process client.
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
enum values are persisted as stable lowercase values. ISRC is nullable, indexed, and non-unique.

For an existing source, Stage 2 fills only missing Track fields. Existing canonical values and a
known ISRC are not overwritten by conflicting or degraded responses. Provider metadata uses an
allowlisted, non-destructive merge and never stores cookies, tokens, or credentials. A provider
identity cannot move to another Track through ordinary upsert.

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

The command validates and canonicalizes one supported track URL, retrieves normalized metadata
from the isolated worker, and persists one provider identity. Repeating the same identity updates
its existing TrackSource without creating a duplicate or orphan Track.

## Current limitations

- OnTheSpot exposes no documented stable Python library API. Only the child worker imports the
  inspected v1.8.1 account loader, URL matcher, metadata registry, and token selector.
- OnTheSpot's required pure-Python Protobuf mode can reduce Protobuf performance inside the child;
  it does not affect the application process.
- The worker deliberately does not auto-respawn after a crash. Its owner must close and replace
  the failed process client during controlled application lifecycle recovery.
- Native codec, container, and bitrate remain unknown when upstream metadata does not expose them
  without entering later stream/download flows.
- Albums, playlists, cross-provider matching, provider/quality resolution, downloads, conversion,
  Telegram upload/cache, queues, and APIs are intentionally absent.
