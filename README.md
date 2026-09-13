<div align="center">

# 🎵 Musicbot Downloader

### Your self-hosted music downloader, inside Telegram.

Search for a track, confirm the recording, choose the quality, and let Musicbot find a safe source and deliver the file back to Telegram.

**Spotify · Deezer · Tidal · YouTube Music · Qobuz · Apple Music · Bandcamp · SoundCloud**

**MP3 128 · MP3 320 · AAC 128 · AAC 256 · Lossless**

<br>

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-Bot-26A5E4?logo=telegram&logoColor=white)
![License](https://img.shields.io/badge/license-Proprietary-lightgrey)

</div>

<!--
Recommended: add a real screenshot here.

<p align="center">
  <img src="docs/assets/telegram-home.png" alt="Musicbot Downloader in Telegram" width="720">
</p>
-->

## What is Musicbot Downloader?

Musicbot Downloader is a **self-hosted Telegram bot for finding and downloading music**.

You can search for a recording or send a supported music link. Musicbot identifies the intended track, evaluates the available providers, selects a source that can safely satisfy the requested quality, processes the media when necessary, and delivers the result directly in Telegram.

The important part is that **search and download are not tied to the same provider**.

A track discovered through Apple Music, Spotify, or another catalog can be acquired from a different compatible source when that is the safest way to produce the requested output.

```text
Search or link
      │
      ▼
Identify the recording
      │
      ▼
Choose the output quality
      │
      ▼
Find a compatible source
      │
      ▼
Download / safely process
      │
      ▼
Telegram cache
      │
      ▼
Your chat
```

---

## ✨ Highlights

### 🔎 Search without provider lock-in

Musicbot uses provider-neutral recording identity. The service where a track was found does not automatically become the service used to download it.

### 🎚️ Choose the quality you actually want

Five explicit output profiles are supported:

**MP3 128 · MP3 320 · AAC 128 · AAC 256 · Lossless**

Musicbot does not silently turn lower-quality audio into a higher-quality label.

### 🔁 Safe provider fallback

If one eligible source fails, the downloader can move through safe same-provider account fallback and then compatible cross-provider alternatives.

### 💾 Telegram-native cache

Completed files are uploaded to a private Telegram cache chat/channel. Compatible future requests can reuse Telegram's stored `file_id` instead of maintaining a permanent local music library.

### 🧭 Persistent download lifecycle

Queues, request history, cache metadata, user state, and provider state survive normal container restarts.

The user-facing lifecycle includes clear states such as Preparing, Waiting, Downloading, Processing, Sending, Delivered, Failed, and Cancelled.

### 🛠️ Built-in administration

Owner/admin Telegram flows cover provider accounts, provider health, worker controls, download visibility, and administrator management.

### 🌍 Localized UX

English and Russian locale catalogs are included, with a deterministic fallback model.

---

## 🎧 Providers

Musicbot currently enables search across eight providers.

| Provider | Search | Account | Current safe media behavior |
| --- | :---: | --- | --- |
| **Spotify** | ✅ | Managed | Discovery and recognition; current native Vorbis/Ogg does not map safely to the five exact application profiles |
| **Deezer** | ✅ | Managed | MP3 or FLAC when exact media evidence supports it |
| **Tidal** | ✅ | Managed | AAC/M4A or FLAC when exact media evidence supports it |
| **YouTube Music** | ✅ | Public | Direct AAC 128 |
| **Qobuz** | ✅ | Managed | Direct genuine lossless + safe lossless-to-lossy conversion |
| **Apple Music** | ✅ | Managed | Direct AAC 256 |
| **Bandcamp** | ✅ | Public | Public MP3 128 |
| **SoundCloud** | ✅ | Public | Public MP3 128 after exact preflight |

**Managed accounts:** Tidal, Deezer, Spotify, Qobuz, Apple Music  
**Public providers:** YouTube Music, Bandcamp, SoundCloud

> A provider being healthy or authenticated does not mean every recording exists there. Availability is checked for the specific source during the request.

---

## 🎼 Quality without fake upgrades

Musicbot exposes exactly five output profiles:

| Requested profile | What Musicbot expects to deliver |
| --- | --- |
| `MP3_128` | MP3 at 128 kbps |
| `MP3_320` | MP3 at 320 kbps |
| `AAC_128` | AAC at 128 kbps |
| `AAC_256` | AAC at 256 kbps |
| `LOSSLESS` | Genuine lossless audio |

The rules are intentionally strict:

- ✅ exact native media is preferred;
- ✅ genuine lossless → requested lossy format is allowed;
- ✅ genuine lossless stays lossless;
- ❌ lossy → lossy transcoding is not used;
- ❌ bitrate upscaling is not used;
- ❌ lossy → "lossless" is never allowed.

The final artifact is probed again before it can become a successful download result.

So when Musicbot says **Lossless**, the pipeline is designed to require genuine lossless media rather than just a lossless container.

---

## 🔗 Tracks, albums, and playlists

Musicbot can accept provider links in addition to Telegram search.

Current collection scope:

| Provider | Tracks | Albums | Playlists |
| --- | :---: | :---: | :---: |
| YouTube Music | ✅ | — | ✅ supported finite playlist forms |
| Qobuz | ✅ | ✅ | — |
| Apple Music | ✅ | ✅ | ✅ |
| Bandcamp | ✅ public | ✅ public | — |
| SoundCloud | ✅ public | — | — |

Collection resolution is deliberately conservative: if Musicbot cannot prove that a supported collection was expanded completely, it rejects the discovery instead of silently downloading only part of it.

A collection's original provider is retained as discovery provenance, but its individual tracks can still use the normal provider-independent download pipeline.

---

## 🚀 Quick start

The recommended way to run Musicbot is with Docker Compose.

### What you need

- Docker Engine
- Docker Compose v2
- Internet access to Telegram and the providers you want to use
- a Telegram bot token
- a private Telegram chat or channel for completed-file caching
- local persistent storage for the application data volume

The production image already contains Python 3.12, FFmpeg, ffprobe, and the locked provider runtime.

### 1 — Clone

```bash
git clone https://github.com/romanpodg/musicbot-downloader.git
cd musicbot-downloader
```

### 2 — Configure Telegram

Create your environment file:

```bash
cp .env.example .env
```

Set the important values:

```env
BOT_TOKEN=your_bot_token
TELEGRAM_CACHE_CHAT_ID=your_private_cache_chat_id
OWNER_ID=your_telegram_user_id
```

Keep `.env` private.

The cache chat/channel should be private and available to the bot for media uploads. `OWNER_ID` is the stable numeric Telegram user ID used for owner-level authorization.

### 3 — Build

```bash
docker compose build
```

### 4 — Create or upgrade the database

```bash
docker compose run --rm --no-deps musicbot alembic upgrade head
```

### 5 — Verify the runtime

```bash
docker compose run --rm --no-deps musicbot python -m app.main --check
```

### 6 — Start

```bash
docker compose up -d
```

Watch the logs:

```bash
docker compose logs -f musicbot
```

That's the normal production startup path.

> Database migrations do **not** run automatically on application startup. This is intentional.

---

## 📱 Using the bot

Once the container is running, open your bot in Telegram and send `/start`.

The main user flow is simple:

1. Tap **Search** or **Recognize**.
2. Enter the track you want or use a supported link.
3. Confirm the correct recording when needed.
4. Choose the desired quality.
5. Follow the same Telegram message as the request moves through its lifecycle.
6. Receive the completed file.

The home experience also exposes:

**Search · Recognize · Downloads · History · Settings**

<!--
Recommended screenshot sequence for the final README:

1. docs/assets/home.png
2. docs/assets/search-results.png
3. docs/assets/track-confirmation.png
4. docs/assets/download-progress.png
5. docs/assets/delivered-file.png
-->

---

## 🔐 Provider accounts

Some providers need account/session configuration.

### Managed providers

- **Tidal**
- **Deezer**
- **Spotify**
- **Qobuz**
- **Apple Music**

Their application-visible lifecycle is managed through owner-only flows, while sensitive provider session state remains owned by the isolated provider runtime.

### Public providers

- **YouTube Music**
- **Bandcamp**
- **SoundCloud**

These do not expose an application-managed user account in the current implementation.

Treat all provider state as sensitive. Do not post credentials, ARLs, cookies, tokens, session files, or provider configuration in GitHub issues or logs.

---

## 🧠 Why provider-independent downloading matters

Most download tools start with a provider URL and remain tied to that provider.

Musicbot treats these as separate questions:

```text
What recording is this?
        ≠
Where did the user discover it?
        ≠
Which source can safely produce the requested file?
```

That distinction lets the application preserve recording identity while making a fresh quality-aware provider decision at download time.

For example, a request admitted from one provider can ultimately be fulfilled by another provider when fallback is safe and the requested quality contract is satisfied.

---

## 💽 Where does Musicbot store data?

The default Compose deployment uses one persistent volume:

```text
musicbot-data
└── /data
    ├── SQLite application state
    ├── provider configuration/session state
    └── other durable runtime state
```

Temporary audio lives under:

```text
/tmp/musicbot
```

and is not the permanent music library.

After successful Telegram upload/cache handling, the local artifact is released.

Container replacement preserves durable application state such as users, tracks, queue/history records, Telegram cache metadata, worker settings, and provider sessions stored in `/data`.

---

## ⚙️ Configuration

The full template is [`.env.example`](.env.example).

A few useful settings:

```env
DOWNLOAD_WORKERS_DEFAULT=2
DOWNLOAD_WORKERS_MAX=8

UPLOAD_WORKERS_DEFAULT=3
UPLOAD_WORKERS_MAX=10

QUEUE_MAX_SIZE=1000
PER_USER_ACTIVE_DOWNLOAD_LIMIT=2

DEFAULT_LOCALE=en
SUPPORTED_LOCALES=en,ru

INTERNAL_API_ENABLED=false
APP_LOG_LEVEL=INFO
```

The defaults also include bounded timeouts, temporary-storage limits, cleanup settings, and per-provider concurrency controls.

For the complete production configuration reference, see [`docs/production.md`](docs/production.md).

---

## 🛡️ Operational model

A few deployment rules are important:

- run one Musicbot runtime per SQLite database;
- do not run multiple replicas against the same database;
- keep `/data` on reliable local persistent storage;
- keep the private Telegram cache private;
- keep `.env` and provider state out of source control;
- do not expose the optional Internal API directly to an untrusted network.

The default container drops Linux capabilities, enables `no-new-privileges`, runs as a non-root UID/GID, and keeps temporary media outside the persistent data volume.

---

## 🔄 Updating

A normal update follows the same controlled startup path:

```bash
git pull
docker compose build
docker compose run --rm --no-deps musicbot alembic upgrade head
docker compose run --rm --no-deps musicbot python -m app.main --check
docker compose up -d
```

Back up the persistent database and provider state before upgrades that matter to you.

Detailed backup, restore, filesystem, migration, and recovery procedures are documented in [`docs/production.md`](docs/production.md).

---

## 🩺 Troubleshooting

### The container does not start

Run:

```bash
docker compose run --rm --no-deps musicbot python -m app.main --check
```

Then inspect:

```bash
docker compose logs --tail=200 musicbot
```

### A provider is configured but a track still fails

Provider account readiness, provider health, source availability, and requested-quality compatibility are separate checks.

A healthy provider may simply not have a safe source for that recording and output profile.

### Musicbot refuses a quality conversion

That may be expected.

The downloader deliberately rejects lossy-to-lossy conversion, bitrate upscaling, fake lossless, and unknown media that cannot satisfy the requested profile safely.

### I restarted the container — did I lose everything?

Normal container replacement keeps the persistent `/data` volume. Temporary media is disposable by design.

---

## 👩‍💻 Development

For local development, use Python 3.12 and `uv`:

```bash
cp .env.example .env
uv sync --locked --extra dev
```

Install the locked provider runtime when required:

```bash
uv sync --locked --extra dev --extra onthespot
```

Run the production-oriented validation path:

```bash
bash scripts/validate-production.sh
```

The project uses Ruff, strict mypy, pytest, Alembic migrations, deterministic credential-free validation, and separate opt-in external provider smoke tests.

---

## 📚 Documentation

If you only want to run the bot, this README should be enough to get started.

If you want the exact contracts and architecture:

- [`docs/stage30-provider-platform.md`](docs/stage30-provider-platform.md) — current authoritative provider platform
- [`docs/production.md`](docs/production.md) — deployment, storage, backups, security, and recovery
- [`docs/release-checklist.md`](docs/release-checklist.md) — release validation
- [`docs/`](docs/) — implementation and architecture history

---

## ⚠️ Legal

Musicbot Downloader interacts with third-party music and messaging services.

You are responsible for using the software in accordance with applicable law, the rights attached to the content you access, and the terms of the relevant third-party services.

The current project metadata declares the software license as **Proprietary**. See [`pyproject.toml`](pyproject.toml).

---

<div align="center">

**Self-hosted · Telegram-native · Provider-independent · Quality-aware**

</div>
