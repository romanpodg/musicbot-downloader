# Stage 30.6.6 — SoundCloud public MP3_128 integration

## Production contract

SoundCloud is enabled only as an accountless, public `MP3_128` provider. Its
single advertised native representation is MP3 in an MP3 container at exactly
128 kbps. It has no AAC, MP3_320, lossless, dynamic-best-audio, or collection
capability.

Search, canonical resolution, source checks, Stage 25 candidate selection,
processing, cache, and delivery use the existing provider-neutral path. A
SoundCloud track URL remains a source identity, not a request to override
quality suitability; a lossless request can therefore execute through Qobuz.

## Public-only worker boundary

The application exposes no SoundCloud account IDs to Stage 25. Inside the
isolated OnTheSpot worker, every SoundCloud search, source check, preflight,
and acquisition temporarily selects only the pinned public 128k runtime
account. A coexisting OAuth account is ignored and an explicit account ID is
rejected for native acquisition.

`prepare_source()` runs token-free yt-dlp metadata preflight with the exact
selector `bestaudio[ext=mp3]`. It admits only one `http_mp3_*` HTTP selection
whose codec, extension, and audio bitrate are exactly MP3, MP3, and 128 kbps.
It returns only normalized media facts; selected-format details and signed URLs
remain child-owned.

The public selector is forced again during acquisition. The existing
application-owned media probe then validates the acquired source against the
Stage 25 plan and validates the final artifact before a successful result can
be cached or delivered. Any preflight or post-acquisition discrepancy fails
the attempt and uses ordinary provider fallback.

## Explicitly deferred

SoundCloud OAuth execution and authorization lifecycle, OAuth M4A/AAC or
best-audio selection, MP3_320, all SoundCloud collections, migrations, new
quality profiles, and lossy transcoding policy changes remain out of scope.
