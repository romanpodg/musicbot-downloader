# OnTheSpot v1.8.1 provider capability matrix

This matrix describes only the implementation pinned at commit
`8ed6cf33ef772e6569d5014237e0fb4ce8b9e45d`. It is not a statement about each
commercial service in general. `Unknown` means the value cannot be established reliably before
OnTheSpot enters its stream/download path.

## Stage 10.4 provider-level health audit

Provider Health is distinct from Stage 4 `TrackSource` validation. It never chooses an arbitrary
track, downloads audio, or proves a quality. The child exposes only status, authentication
requirement, download support, and an allowlisted diagnostic code. Account identity and upstream
objects never cross JSONL. On each coalesced sweep, the single child reloads deployment-owned
OnTheSpot configuration and rebuilds its runtime pool with the pinned `AccountPoolLoader` before
the eight normalized inspections. This performs the same bounded account/session initialization
used by normal runtime, but it does not run broad searches or download media. It may refresh Tidal
or Spotify session state and may persist SoundCloud client data as the pinned loader normally does.

| Provider | Native download auth | Provider-level readiness mechanism | Network behavior | Missing/invalid auth | What READY proves / limitation |
| --- | --- | --- | --- | --- | --- |
| Apple Music | Required; active subscription required | Active account-pool entry, usable selected session, and pinned `account_type == premium` established by `/me/account?meta=subscription` during login | Login fetches the web developer token and account/subscription endpoint | No account/session or free tier → `AUTH_REQUIRED`; loader failure → `AUTH_REQUIRED` | `READY` proves the initialized premium session object is present. It does not prove a song has playback assets or a license key. |
| Bandcamp | No user auth | Active public bootstrap entry created after the pinned connectivity ping | Initialization GETs `bandcamp.com`; health itself does not search | Failed/missing public runtime → `UNAVAILABLE` | `READY` proves the public runtime initialized, not that a track page has an MP3 URL. |
| Deezer | Required by the pinned native path, including its configured/public ARL session | Active pool entry and non-null selected login dictionary created after `deezer.getUserData` | Initialization may fetch a public ARL and calls Deezer user data | Missing/failed session → `AUTH_REQUIRED` | `READY` proves the ARL-derived runtime session initialized. Entitlement and exact FLAC/MP3 selection remain source-specific. |
| Qobuz | Required | Active pool entry and token structure are observable, but v1.8.1 login only pings `qobuz.com` and trusts saved token/app values | Initialization performs only the connectivity GET; health adds no speculative endpoint | Missing/failed runtime → `AUTH_REQUIRED`; active but unvalidated → `UNKNOWN / SESSION_UNVERIFIED` | Provider-level `READY` is intentionally not emitted. Only a real source/file call can validate the saved authorization and entitlement. |
| SoundCloud | No user auth; OAuth optional | Active public/OAuth pool entry with selected client data; OAuth is validated by the pinned session endpoint during login | Initialization scrapes current client data; optional OAuth validation is networked and may persist refreshed client data | Failed public/OAuth initialization → `UNAVAILABLE` | `READY` proves current public/OAuth bootstrap succeeded, not that a particular transcoding is streamable. |
| Spotify | Required | Active librespot session returned by `get_account_token`; the selector may run pinned bounded session reinitialization | Session construction/reinitialization is networked and can refresh normal runtime state | Missing or failed session → `AUTH_REQUIRED` | `READY` proves a live session object and tier fact are present. It does not prove a specific audio key can be loaded; free-tier Vorbis may also fail Stage 5 quality policy. |
| Tidal | Required | Active token/country runtime entry after the pinned expiry/refresh loader | Initialization pings Tidal and refreshes expired tokens over OAuth, persisting normal refresh state | Missing/failed refresh/session → `AUTH_REQUIRED` | `READY` proves the loader accepted current token state. Pinned code does not validate an unexpired token against a separate provider-level endpoint, and source subscription/region checks remain later. |
| YouTube Music | No user auth | Active public yt-dlp bootstrap entry created after connectivity succeeds | Initialization GETs `youtube.com`; health itself does not run extraction | Failed/missing public runtime → `UNAVAILABLE` | `READY` proves public bootstrap initialized, not that yt-dlp can extract a particular non-live public item. |

All `READY` states mean only that the provider-level native runtime appears usable enough to make
an attempt when a suitable verified source exists. Stage 4/5/6 still recheck source availability,
media facts, quality policy, and acquisition. Provider Health has no persistence, no background
poller, and no dependency edge into the resolver, pipeline, workers, SingleFlight, or Telegram
cache.

| Provider | Metadata | Search | Download implementation | Account/runtime requirement | Pre-download media facts and limitations |
| --- | --- | --- | --- | --- | --- |
| Apple Music | Registered catalog metadata; `playParams` supplies playability | Registered | Fetches web-playback flavor `28:ctrp256`, decrypts to M4A | User media token and an active subscription | Pinned path is AAC in M4A at 256 kbps. A logged-in free account is normalized to `AUTH_REQUIRED`; no lossless path is implemented. Source metadata can report unavailable. |
| Bandcamp | Scrapes the track page and its `mp3-128` file URL | Registered | Direct HTTP stream from metadata `file_url` | Public bootstrap; no user credentials | Pinned path is MP3 at 128 kbps. Upstream sets `is_playable = false` only for some malformed file mappings, so a missing URL can still escape the preflight and fail later. |
| Deezer | Public catalog metadata plus authenticated fallback | Registered | Inspects source file-size fields and internally chooses FLAC, MP3 320, MP3 256, or MP3 128; failed selection falls back to MP3 128 | ARL-derived session/license token; login detects free, HQ, or lossless entitlement | The implementation supports MP3 and FLAC, but the exact source result is unknown until its later selection call. Its `1411k` FLAC label is not treated as a fixed native bitrate. |
| Qobuz | Authenticated track/album calls; `streamable` supplies playability | Registered | Requests `getFileUrl` with fixed format id 27 and writes a FLAC path | Configured email/password plus app ID, secrets, and user auth token | Pinned path requests FLAC. Login only pings Qobuz and does not validate all credentials, so an active pool entry can still fail a source check; such ambiguous failures are `ERROR`, not guessed as auth or source failure. Sample rate and bit depth are unknown. |
| SoundCloud | Authenticated public-client API metadata; `streamable` supplies playability | Registered | yt-dlp; public mode requests MP3, OAuth mode prefers M4A then MP3 then best audio | Default public bootstrap obtains client data; optional OAuth changes available media | Public path is MP3 at 128 kbps. OAuth media is source-dependent and exact native format remains unknown until yt-dlp extraction. No lossless path is implemented. |
| Spotify | Authenticated Web API/librespot metadata; catalog `is_playable` is used when present | Registered | librespot `VorbisOnlyAudioQuality`, HIGH for free and VERY_HIGH for premium tracks | Configured librespot session | Pinned path is Ogg Vorbis at 160 or 320 kbps according to the selected account tier. No lossless path is implemented. Availability may still fail later if librespot substitutes or rejects the track. |
| Tidal | Authenticated track/album metadata; `streamReady` supplies playability | Registered | Requests manifests in `LOSSLESS`, `HIGH`, then `LOW` order; handles direct FLAC/MP4 URLs or an MPD through yt-dlp | Configured access token, refresh flow, and country code | Lossy and lossless paths exist, but the chosen source codec, container, bitrate, sample rate, and bit depth are unknown until manifest selection. OnTheSpot silently falls back across qualities. |
| YouTube Music | yt-dlp metadata-only extraction; public, non-live availability supplies playability | Registered | yt-dlp requests best audio in M4A | Default public bootstrap; no user credentials | Pinned path labels AAC/M4A at 128 kbps. Private, unavailable, and live items are not usable. |

## Stage 9.3 album snapshot audit

Album support is based only on the pinned OnTheSpot registries and provider implementations. The
application stores a provider-specific snapshot; it does not infer a canonical album or merge
releases between providers. Ordered item identity is mandatory, snapshots are capped at 500 tracks,
and provider dictionaries, credentials, tokens, manifests, and media URLs remain inside the child
process.

| Provider | Album URL detection and ordered listing | Runtime/auth requirement | Snapshot completeness and known limitations |
| --- | --- | --- | --- |
| Apple Music | Native album URL matcher and registered album-track ID function | Active Apple Music session, user media token, and subscription | Stable album and track IDs, disc/track order, title, artist, album, and duration are normally available through per-track metadata. Album title/artist are inferred from the returned tracks. |
| Bandcamp | `/album/<slug>` matcher and public album-page scrape | Public bootstrap; no user credentials | Ordered track URLs and title/artist/track number are available. Duration, release date, disc number, and a stable non-URL track ID may be absent; the canonical album URL is retained as provider identity. |
| Deezer | Numeric album matcher and registered album-track listing | Public album listing; active Deezer session is used for complete per-track metadata and fallback behavior | Ordered IDs plus title, artist, album, disc, track number, duration, and release metadata are generally available. Catalog/session restrictions can still fail resolution. |
| Qobuz | Album path/ID matcher and registered album-track listing | Configured authenticated Qobuz account/session | Ordered stable IDs and rich per-track metadata are normally available. Account or regional restrictions can make the album unavailable. |
| SoundCloud | URL classification is delegated to the isolated worker; upstream album releases are playlist-shaped objects marked as albums | Public runtime token/account; optional OAuth may change catalog access | Ordered track IDs and per-track metadata are available when upstream identifies the set as an album. Ordinary SoundCloud playlists are rejected. URL shape alone is not trusted. |
| Spotify | 22-character album matcher and paginated registered album-track listing | Configured Spotify/librespot session | Ordered stable IDs, disc/track numbers, title, artist, album, duration, and release metadata are normally available. Market/account availability still applies. |
| Tidal | Numeric/stable album matcher and registered album-track listing | Configured access/refresh token and country session | Ordered stable IDs and rich per-track metadata are normally available. Regional and subscription restrictions still apply. |
| YouTube Music | No pinned album matcher or album-track listing contract | N/A | Unsupported for Stage 9.3. YouTube playlists are intentionally not treated as albums. |

For supported providers, the child first obtains the ordered provider track identities, then returns
only allowlisted normalized display metadata for each item. Missing optional fields stay `None`;
the application neither fabricates values nor fuzzy-matches the full release eagerly. Each selected
item is canonicalized later and may download from any currently eligible verified source.

## Runtime interpretation

Stage 4 checks one verified `TrackSource` at a time through the serialized child process. It first
requires an active OnTheSpot account-pool entry, distinguishing missing user authentication from
failed public-provider initialization. It then invokes the pinned track metadata function and uses
an explicit `is_playable = false`, HTTP 404, or normalized authentication response when available.
No media bytes, manifests, file URLs, credentials, sessions, or raw upstream dictionaries are
returned to the application process.

The source check cannot prove that a later stream request will succeed. In particular, account
tier, regional catalog rules, expiring sessions, and provider-specific stream endpoints can change
between Stage 4 and a future download. Stage 4 therefore reports current observable facts and
keeps unknown values as `None`; it does not rank providers or satisfy a delivery quality profile.
For Stage 5, the normalized capability model also lists only bounded potential native
representations supported by the pinned implementation. These possibilities are never treated as
confirmed media: they can create only `REQUIRES_PREFLIGHT` plans whose codec, bitrate, or genuine
lossless constraint must be checked by Stage 6.

## Stage 6 native-download audit

Stage 6 uses the pinned `DownloadWorker._download` service dispatcher inside the isolated child
and deliberately does not call `_finalize_audio`. Consequently `raw_media_download`,
`track_file_format`, `file_bitrate`, `convert_audio_format`, metadata embedding, artwork fetching,
and M3U behavior cannot post-convert the source. The worker writes a controlled `native.partial`
inside the current job, completes required provider-native decryption, atomically renames it, and
returns only normalized media hints and the controlled path. The application then probes the real
file; hints never prove quality by themselves.

| Provider | Pinned native acquisition | Preflight before full audio | Native/decryption facts | Stage 6 limitations |
| --- | --- | --- | --- | --- |
| Apple Music | Web-playback flavor `28:ctrp256`, yt-dlp transport fetch | Fixed account/flavor facts only; file is probed after download | AAC/M4A 256; FFmpeg decrypts with provider key using `-c copy` | Requires premium account and upstream FFmpeg for provider-native decryption |
| Bandcamp | Direct HTTP from metadata `file_url` | Catalog metadata fixes MP3 128; no separate stream probe | Public MP3 128; no decryption | Missing/expired URL is recoverable only at download time |
| Deezer | Media endpoint chooses FLAC/MP3, then native Blowfish decrypt | Yes: source file-size fields select the intended native representation | FLAC or MP3 128/256/320; provider-native decryption | Media URL selection can fall back to MP3 128 after preflight, so the downloaded file is always reprobed |
| Qobuz | `getFileUrl` format id 27, direct HTTP | Capability/account state fixes the FLAC path; URL resolution happens during download | FLAC; no application transform or decryption | Sample rate and bit depth are known only after probe |
| SoundCloud | yt-dlp direct audio selection | Public path is fixed MP3 128; OAuth-dependent selection requires download-before-probe | Public MP3 128; OAuth can select M4A/MP3 | OAuth exact media is not claimed by preflight |
| Spotify | librespot `VorbisOnlyAudioQuality` and account-tier quality | Account tier determines HIGH 160 or VERY_HIGH 320 before download | Native Ogg Vorbis; librespot handles provider transport | Alternative-track/session failures remain recoverable; no approved MP3/AAC conversion from Vorbis |
| Tidal | LOSSLESS/HIGH/LOW manifest selection; direct URL or local MPD through yt-dlp | Yes: Stage 6 inspects the selected manifest for FLAC versus MP4/AAC before full audio | FLAC or AAC/M4A; token headers remain inside child | Upstream can fall back between qualities and manifests can expire/change, so actual media is always reprobed |
| YouTube Music | yt-dlp `bestaudio[ext=m4a]` | Fixed implementation expectation only; download-before-probe | AAC/M4A nominal 128 | Extractor availability and actual bitrate can change upstream |

“Preflight” here means media facts can be established without fetching the complete audio. Every
provider still receives final application-owned ffprobe validation. No stream URL, manifest,
account token, cookie, or raw upstream dictionary crosses JSONL IPC.
