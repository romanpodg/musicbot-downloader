# Stage 30.2 — YouTube Music vertical integration

Stage 30.2 promotes YouTube Music to the application search registry as the
fourth provider and completes the track-only vertical slice: free-text search,
Stage 29 recognition/enrichment, strict `music.youtube.com/watch` links,
canonical resolution, source playability checks, native download, and durable
Telegram delivery/cache behavior.

The isolated runtime is OnTheSpot commit `8ed6cf33ef772e6569d5014237e0fb4ce8b9e45d`
(documented upstream v1.8.1). YouTube Music uses the tokenless public account
`public_youtube_music`; search is yt-dlp `ytsearch` and provider identity is the
YouTube video ID. Metadata is normalized from title, channel/artist, album or
title fallback, duration, playability, and available year evidence. ISRC and
explicit status are not invented when absent.

Only `https://music.youtube.com/watch?v=<video-id>` is accepted. Generic
YouTube hosts, short links, playlists, channels, and albums remain outside the
scope. Runtime readiness requires an initialized searchable public runtime;
failure is `UNAVAILABLE`, never `AUTH_REQUIRED`, and health never bypasses the
source-specific `check_source()` playability check.

The application integration manifest is Spotify, Deezer, Tidal, YouTube Music
for search orders 0–3. Provider Accounts remains Tidal, Deezer, Spotify; YTM is
`NOT_REQUIRED` and has no authorization or credential flow. Search/recognition
provider identity remains independent from download-provider selection.

`QualityProfile.AAC_128` is an exact AAC/M4A/128-kbps lossy profile. A native YTM
source produces a direct `NATIVE_EXACT_MATCH` plan. AAC 256, MP3, and lossless
requests cannot be satisfied from YTM AAC 128 (no upscaling, lossy-to-lossy
conversion, or false lossless claim). A genuine lossless source may safely
transcode to AAC/M4A 128 through the existing application FFmpeg boundary;
direct exact plans rank before that transcode fallback. Existing generic source
bitrate tolerance and provenance checks are unchanged.

AAC 128 is carried through quality keyboards/callbacks, queue and delivery
models, artifact fingerprints, SingleFlight, Telegram cache, retries, and the
`20260906_0020` Alembic migration. The migration preserves legacy rows and its
downgrade refuses deterministically when any affected table contains AAC 128,
because rewriting it to another codec would be dishonest.

The optional external smoke test is credential-free but network-dependent:

```text
ONTHESPOT_YTM_TEST_TRACK_URL="https://music.youtube.com/watch?v=..." \
  uv run pytest -m external -k youtube
```

Albums, playlists, channels, generic YouTube URLs, cookies/Premium, managed
accounts, AAC 256/lossless YTM claims, and activation of Qobuz, Apple Music,
Bandcamp, or SoundCloud application search are explicit non-goals.
