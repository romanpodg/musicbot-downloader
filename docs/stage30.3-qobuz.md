# Stage 30.3 — Qobuz vertical integration

Qobuz is the first managed premium lossless provider. Unlike YouTube Music,
Qobuz requires an OWNER-managed email/password account and exposes native FLAC.
Credentials are submitted through the existing sensitive Telegram flow and are
owned and persisted only by the isolated OnTheSpot runtime.

The child authenticates, reloads its account pool, and performs a read-only
authenticated catalog operation before the application reports `READY`.
Missing, invalid, expired, or unverified sessions remain sanitized health
states; stored credentials alone never imply readiness. Reset and startup
reconciliation reuse the existing provider-account lifecycle.

The Stage 30.1 manifest promotes Qobuz to managed account order 3 and search
order 4. Ordering is presentation/search ordering only; it is not provider
priority, canonical identity ranking, or download preference. Search uses the
generic `RuntimeTrackSearchAdapter`, and Qobuz candidates participate in Stage
29 enrichment and recognition without provider bias or persistence before the
explicit Download confirmation.

Qobuz track sources are checked through `check_source()` before resolution and
download. Native FLAC satisfies `LOSSLESS` directly. Requests for `AAC_128`,
`AAC_256`, or `MP3_320` cross the existing transcoder boundary; no Qobuz
shortcut or new quality profile is introduced.

The optional credentialed smoke path is intentionally outside normal CI:
`QOBUZ_EXTERNAL_SMOKE=NOT_RUN` unless a real account is supplied and the full
authenticated search → metadata → source check → FLAC download → ffprobe flow
is executed. Playlists, album batches, artist pages, payment, and other future
providers remain out of scope.
