# Stage 22 — User download preferences

Stage 22 stores one validated preference row per user and resolves it against
the selected provider and track capabilities. Defaults are `BEST_AVAILABLE`,
`ORIGINAL`, `AUDIO`, metadata enabled, and cover enabled; reading defaults does
not create a row.

Quality is provider-neutral: best available ranks lossless, high, then
standard; lossless prefers lossless and falls back deterministically; high
prefers high lossy; standard selects the ordinary tier. `ORIGINAL` preserves
the native pipeline format. Explicit formats are accepted only when the
capability set advertises them, and lossless combined with MP3/M4A is rejected
as an invalid combination.

Admission persists the requested and effective profile, delivery presentation,
metadata flags, and fallback reason on `download_requests`. These fields are
nullable for pre-Stage-22 rows, so existing Stage 21 jobs retain their prior
behavior and workers never consult live settings.

The settings command is intentionally compact and ownership-bound. Stage 22
does not add batch downloads, caching, cross-provider fallback, new providers,
or advanced media processing.
