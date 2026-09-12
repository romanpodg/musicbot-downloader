# Stage 30.6.5 — SoundCloud media preflight and product-scope resolution

## Final status

**`PUBLIC_MP3_128_ONLY_READY`**

Secondary qualification: **OAuth is `EXECUTION_BINDING_BLOCKED`**.  SoundCloud
remains deferred in the application registry.  This stage does not enable
search, downloads, account management, Telegram presentation, or collections.

The result is deliberately narrower than a general SoundCloud integration: the
pinned public path is an MP3/128-only candidate for the existing `MP3_128`
profile, while the pinned OAuth path cannot currently carry a selected format
from metadata preflight into acquisition.

## Pinned provenance

| Component | Pinned evidence | Relevant implementation |
| --- | --- | --- |
| OnTheSpot | `8ed6cf33ef772e6569d5014237e0fb4ce8b9e45d` | `pyproject.toml`, `uv.lock`; `onthespot/api/soundcloud.py`, `onthespot/downloader.py` |
| yt-dlp | `2026.7.4` / package `2026.07.04` | `uv.lock`; locked wheel contains `yt_dlp/extractor/soundcloud.py` and `yt_dlp/YoutubeDL.py` |
| metadata and playability | SoundCloud v2 `tracks/{id}`; normalized `streamable` | `onthespot/api/soundcloud.py::soundcloud_get_track_metadata` |
| acquisition | `DownloadWorker._download_via_ytdlp_audio` | `onthespot/downloader.py` |
| bootstrap/authentication | `soundcloud_login_user`, then `AccountPoolLoader` | `onthespot/api/soundcloud.py`, `onthespot/accounts.py` |

The lock, not current upstream OnTheSpot or yt-dlp, is the authority for this
document.

## Complete pinned media paths

### Public / anonymous path

1. The default config contains the active `public_soundcloud` account.  Its
   bootstrap scrapes the SoundCloud home page and a script asset for `client_id`
   and `app_version`, persists those values in the child-owned OnTheSpot
   configuration, and adds a public (`128k`) account-pool entry.
2. Metadata resolution calls the v2 track endpoint with that client data.  The
   normalized `streamable` field is the pinned playability input.  It does not
   expose a transcoding URL across IPC.
3. The downloader receives the canonical SoundCloud item URL and configures
   yt-dlp with `bestaudio[ext=mp3]`.  It sets its declared native result to
   `.mp3` and `128k` before it calls yt-dlp.
4. yt-dlp's SoundCloud extractor resolves the track's `media.transcodings`,
   calls the corresponding SoundCloud transcoding endpoint, and constructs
   audio formats with `format_id`, `ext`, `acodec`, `abr`, `protocol`,
   `container`, and a stream URL.  The stream URL is signed/temporary runtime
   material and stays inside the child.

The selector is deterministic for a given extractor result, but OnTheSpot does
not persist or pass yt-dlp's selected format ID.  A format may disappear or be
reordered between calls.  Public product activation must therefore force the
public account path and use the same public selector on every execution; it
must reject an unexpected final probe rather than deliver it.

### OAuth / authenticated path

1. An OAuth token is optional in the pinned runtime.  `soundcloud_add_account`
   stores it under the OnTheSpot account's `login.oauth_token`; bootstrap
   refreshes client data and validates it against SoundCloud's session endpoint.
   A valid token creates a `premium` / `256k` account-pool entry.
2. Track metadata still comes from the v2 track endpoint.  The pinned metadata
   function deliberately does not resolve a transcoding URL (its old direct
   attempt is disabled because of DRM failures).
3. Acquisition gives yt-dlp `username="oauth"` and the OAuth token as the
   password, with the broad selector
   `bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio`.
4. OnTheSpot calls `extract_info(item_url)` with its default `download=True`,
   reads `abr` and `audio_ext` from that result, then invokes
   `downloader.download(item_url)` independently.  It neither retains nor
   supplies a format ID, representation ID, or opaque token to the download
   call.

Consequently the OAuth result may be M4A/AAC, MP3, or another best-audio format
depending on the current extractor response.  The application must not create
an OAuth `QualityPlan` from the account's nominal `256k` label.

## Pre-download media truth

The locked yt-dlp API supports `YoutubeDL.extract_info(url, download=False)`.
When `process=True` (the default), its SoundCloud extractor resolves a concrete
format list before audio bytes are downloaded.  A selected format can expose:

| Field | Public selector | OAuth selector | Classification |
| --- | --- | --- | --- |
| selected `format_id` | available from yt-dlp metadata | available from yt-dlp metadata | `SOURCE_VERIFIED` |
| codec / `acodec` | available | available | `SOURCE_VERIFIED` |
| extension / container | available (with yt-dlp's `m4a_dash` container nuance) | available | `SOURCE_VERIFIED` |
| audio bitrate / `abr` | available when supplied by the extractor | available when supplied by the extractor | `PARTIALLY_VERIFIED` |
| protocol | available | available | `SOURCE_VERIFIED` |
| signed stream URL | available to the extractor | available to the extractor | `SOURCE_VERIFIED`, but must remain child-only and ephemeral |
| final downloaded bytes | unavailable before acquisition | unavailable before acquisition | `UNAVAILABLE_BEFORE_DOWNLOAD` |

`abr` is extractor metadata, not a substitute for the application-owned final
probe.  A future preflight must require a positive exact bitrate and reject
unknown, approximate, or mismatched values.

### Token-free runtime probe

Using the locked yt-dlp `2026.7.4` archive, a metadata-only probe against the
public track `soundcloud.com/forss/flickermood` used the exact pinned public
selector.  It selected:

```
extractor=Soundcloud; format_id=http_mp3_0_0; ext=mp3; acodec=mp3;
abr=128; protocol=http; container=NA
```

No media was downloaded and no stream URL, client ID, token, cookie, or account
identifier was printed or persisted.  This is `RUNTIME_VERIFIED` evidence for
that public item and `SOURCE_VERIFIED` evidence for the locked selection path.
It is not a claim that every SoundCloud item will remain available forever.

## Existing exact-profile mapping

| Discoverable media form | Mapping |
| --- | --- |
| public selected MP3, MP3 container, exactly 128 kbps | `EXACT_EXISTING_PROFILE`: `MP3_128` direct |
| OAuth selected AAC/M4A, exactly 128 kbps | `EXACT_EXISTING_PROFILE`: `AAC_128` direct, only after binding exists |
| OAuth selected AAC/M4A, exactly 256 kbps | `EXACT_EXISTING_PROFILE`: `AAC_256` direct, only after binding exists |
| OAuth selected MP3, MP3 container, exactly 128 kbps | `EXACT_EXISTING_PROFILE`: `MP3_128` direct, only after binding exists |
| OAuth selected MP3, MP3 container, exactly 320 kbps | `EXACT_EXISTING_PROFILE`: `MP3_320` direct, only after binding exists |
| missing codec/container/positive exact bitrate; Opus/WebM; non-exact bitrate | `INSUFFICIENT_MEDIA_TRUTH` or `NO_SAFE_EXISTING_PROFILE` |
| any lossy source proposed for conversion to another lossy profile or `LOSSLESS` | `NO_SAFE_EXISTING_PROFILE` |

No `QualityProfile` is added or reinterpreted by this stage.

## Stage 25 seam and execution binding

The existing seam is sufficient in shape but not in OAuth binding strength:

```text
ProviderResolver.check_source()
  -> QualityResolver plans_for_candidate()
  -> DownloadPipeline.check_source()
  -> DownloadPipeline.prepare_source() for REQUIRES_PREFLIGHT
  -> DownloadPipeline.download_source()
  -> application-owned final media probe
```

`check_source()` remains the authoritative availability check.  It already
returns public SoundCloud's bounded MP3/128 declaration only when the selected
runtime account is public; for OAuth it returns no native media declaration.
`prepare_source()` is the correct narrow location for a future media descriptor,
but its current SoundCloud implementation returns MP3/128 for public accounts
and `None` for OAuth.  `PreparedSourceMedia` has no selected-format identity,
and `download_source()` accepts no preflight token or format ID.

Thus no provider-neutral framework is warranted in this audit, but full OAuth
would need one small explicit binding extension: a sanitized, stable format
identity (or another deterministic opaque execution selector) produced by
preflight, passed only through the isolated worker to acquisition, and checked
again against the exact selected result.  It must never carry a raw media URL.
The worker must handle changed/expired formats as a failed attempt followed by
ordinary Stage 25 fallback; it must never silently choose a different format.

## Authentication, storage, and product boundaries

Public operation is accountless from the application's perspective, but it is
not networkless: the child discovers current public client data and persists
that client data in OnTheSpot-owned configuration.  OAuth is optional and is
not activated automatically by the application; it requires an explicit
OnTheSpot account record containing an OAuth token.  The current application
has no SoundCloud user-managed authorization lifecycle and must not add one in
this stage.

The isolated worker boundary is the correct ownership boundary for client data,
OAuth tokens, cookies, signed transcoding URLs, and account identifiers.
Existing sanitized JSONL methods must continue to return only status, native
media facts, and a future non-secret format identity.  Signed URLs may expire,
can include authorization/query material, and must not enter application logs,
SQLite, DTOs, callbacks, Telegram output, audit records, or errors.

One activation safeguard is still required: the later public-only stage must
make selection explicitly public-only.  The pinned account pool can contain an
OAuth entry and current account selection is not an OAuth exclusion mechanism.
The public integration must select/allow only the immutable public account path
for SoundCloud and treat the presence of OAuth state as irrelevant, rather than
letting account ordering change media selection.

## Product decision and next stage

Public-only activation is feasible only as a single exact capability:

```text
advertised capability: MP3_128 only
execution account: forced public bootstrap/account only
execution selector: bestaudio[ext=mp3]
acceptance: exact MP3/128 final probe; otherwise fail the attempt
```

This guarantees OAuth/dynamic media cannot silently produce an approved public
plan once the account-selection safeguard is implemented.  It does not make
OAuth safe.  Full OAuth is not production-ready because it lacks a stable
preflight-to-execution binding, and it also lacks a user-managed lifecycle.

The one recommended next stage is **30.6.6 — SoundCloud Public MP3_128 Vertical
Integration**.

Its scope is registration through the existing provider-neutral search,
canonical-resolution, `check_source()`, Stage 25, and final-probe path; an
explicit public-only worker/account-selection guard; exact `MP3_128` direct
plans; and deterministic regression tests proving OAuth state cannot alter the
public execution contract.  It excludes OAuth support, OAuth account flows,
all SoundCloud collections (albums, sets, and playlists), new quality profiles,
and any generic preflight/binding abstraction.  A later OAuth stage may add the
small binding seam only after it can prove selected-format replay.

## Validation boundary

This stage changes documentation only.  Deterministic validation must cover
the existing SoundCloud-deferred integration policy, worker source checks,
quality planning, and Stage 25 selection.  The metadata probe above is a
token-free external runtime observation, separate from deterministic tests.
No Docker/container acceptance or credentialed OAuth smoke is claimed here.
