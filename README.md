# Musicbot Downloader

Production-oriented foundation for a future Telegram music downloader service. This repository
currently implements Stage 0, Stage 1, and the metadata-only portion of Stage 2. It does **not**
contain a Telegram bot, downloader pipeline, worker queues, internal API, or quality resolver.

## Architecture

The core identity boundary is:

```text
Track identity != search provider != download provider
```

`tracks` stores provider-independent metadata. Provider identities and URLs live in
`track_sources`, so one track can later be associated with Spotify, Deezer, Tidal, Qobuz, and
other sources without provider columns being added to `tracks`. The four delivery profiles are
`MP3_128`, `MP3_320`, `AAC_256`, and `LOSSLESS`; native codec/container information is modeled
separately and is never treated as a requested delivery quality.

Application services consume the local `MusicProvider` abstraction. Only
`app/providers/onthespot/` imports OnTheSpot internals.

## Internationalization

Locale resources are JSON files under `app/i18n/locales/<locale>/`. The localization service is
independent of Telegram and resolves a locale in this order:

1. supported explicit user preference;
2. supported Telegram language code (including values such as `ru-RU`);
3. configured default locale.

Translation lookup falls back to the default locale and then deterministically returns the
translation key. Business services and domain exceptions do not contain translated UI text.

## Requirements and setup

- Python 3.12 or newer
- `uv` (recommended) or `pip`
- SQLite (included with Python)
- Git only when installing the optional pinned OnTheSpot dependency

```bash
cp .env.example .env
uv sync --extra dev
```

For live OnTheSpot metadata resolution, install its optional dependency tree:

```bash
uv sync --extra dev --extra onthespot
```

OnTheSpot is pinned to v1.8.1 commit `8ed6cf33ef772e6569d5014237e0fb4ce8b9e45d`.
It is optional because its current package pulls GUI and download dependencies (including PyQt)
even for metadata-only use. Configure provider accounts using OnTheSpot's own supported account
configuration before resolving providers that require authentication. Credentials remain in
OnTheSpot's configuration; this application never copies them into its database.
The adapter selects Protobuf's pure-Python implementation before initializing OnTheSpot. This is
needed because the pinned `librespot` contains legacy generated descriptors while OnTheSpot's
`pywidevine` requires a modern Protobuf release; their version requirements cannot be satisfied by
a Protobuf downgrade.

`OWNER_ID` may be blank. On startup, an existing user with that Telegram ID is promoted to
`OWNER`. If the user has not yet been observed, no placeholder row or fake Telegram profile is
created; a later user-observation flow can create the real row and rerun bootstrap.

## Database migrations

Production schema changes use Alembic, not `Base.metadata.create_all()`:

```bash
uv run alembic upgrade head
```

SQLite connection initialization centrally enables WAL mode, foreign keys, and a 5000 ms busy
timeout. The repository interfaces and domain services are not coupled to SQLite-specific SQL.

## Development checks

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy app
uv run pytest -m "not external"
```

External provider tests are opt-in and skipped by default. To exercise a configured live account:

```bash
ONTHESPOT_TEST_TRACK_URL="<TRACK_URL>" uv run pytest -m external
```

## Resolve tool

After applying migrations and installing/configuring OnTheSpot:

```bash
uv run python -m app.tools.resolve "<TRACK_URL>"
```

The command validates a single-track URL, asks OnTheSpot for metadata, normalizes it, creates or
finds a provider-independent `Track`, and upserts its `TrackSource`. Repeating the same provider
track identity updates the existing source rather than creating a duplicate.

## Current limitations

- OnTheSpot does not expose a documented stable library API. The adapter uses its internal
  `UrlMatcher`, account loader, metadata registry, and token selector behind one lazy bridge.
- OnTheSpot's metadata calls are synchronous and rely on global configuration/account state; the
  adapter serializes and runs them in a worker thread to avoid blocking the async event loop.
- The required Protobuf compatibility mode may make Protobuf-heavy upstream operations slower.
- Authenticated providers require accounts configured in OnTheSpot. Absence of credentials is
  reported as a domain authentication error.
- Native codec, container, and bitrate remain unknown at metadata-only resolution because current
  upstream metadata functions do not expose stream representation details without entering later
  download/stream flows.
- Albums, playlists, cross-provider resolution, quality selection, downloading, Telegram upload,
  caching, queues, and APIs are intentionally not implemented in this stage.
