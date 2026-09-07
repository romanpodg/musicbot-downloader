# Stage 30.4.1 — Apple Music managed vertical integration

Apple Music is promoted to a full, managed provider using the pinned OnTheSpot
runtime at commit `8ed6cf33ef772e6569d5014237e0fb4ce8b9e45d`. The runtime
requires one OWNER-supplied `media-user-token`; it derives the developer token
from Apple Music web-player assets in the isolated child. `/me/account?meta=subscription`
is required for READY and a paid subscription is required.

Authorization uses the Stage 30.4.0 lifecycle seam and the typed
`APPLE_MUSIC_SESSION_TOKEN` method. The token is wrapped in `SensitiveValue`,
crosses a bounded IPC operation, and is persisted only by OnTheSpot. The child
rebuilds its session and returns sanitized state. Reset and startup reconciliation
remain provider-scoped.

The manifest enables generic runtime search at order 5 and managed account
presentation at order 4. Apple candidates enter Stage 29 enrichment through the
provider-neutral metadata model (title, artist, album, duration, ISRC, explicit,
and playability evidence). Recognition never persists a Track or TrackSource and
never pins the eventual download provider. Only track URLs are admitted in this
stage; album-shaped `?i=<track-id>` links retain the existing parser behavior,
while album and playlist collection references are not newly batch-executed.

Apple `check_source()` remains authoritative per track. The pinned runtime
selects the `28:ctrp256` encrypted stream, performs provider-side Widevine
decryption, and returns native AAC/M4A 256 kbps. This decryption is not a quality
transcode; the application validates the resulting artifact with its existing
ffprobe/media boundary and performs no re-encode for an exact `AAC_256` request.
Apple cannot satisfy `AAC_128`, `MP3_128`, `MP3_320`, or `LOSSLESS`; no lossy-to-
lossy conversion or fake lossless path is introduced. Genuine lossless providers
remain eligible through normal resolution and quality ranking.

No migration, dependency upgrade, pinned-runtime change, new quality profile,
provider preference, Apple lossless/ALAC, Atmos, collection expansion, Bandcamp,
SoundCloud, or Stage 30.5 functionality is included. External Apple smoke is
credential-dependent and reported separately from deterministic tests.
