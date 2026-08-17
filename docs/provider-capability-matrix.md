# OnTheSpot v1.8.1 provider capability matrix

This matrix describes only the implementation pinned at commit
`8ed6cf33ef772e6569d5014237e0fb4ce8b9e45d`. It is not a statement about each
commercial service in general. `Unknown` means the value cannot be established reliably before
OnTheSpot enters its stream/download path.

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
