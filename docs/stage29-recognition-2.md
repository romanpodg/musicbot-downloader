# Stage 29 — Recognition 2.0

Stage 29 hardens the deterministic Stage 17/18 recognition and confirmation
flow; it does not replace it. Search remains lightweight and provider-neutral.

## Contract

Recognition runs two passes. The first ranks catalog candidates, then at most
five preliminary finalists are enriched through the existing
`MusicProvider.get_track_metadata(provider, provider_track_id)` boundary. The
second pass ranks the enriched set. Metadata failures are isolated and the
lightweight candidate remains usable. Enrichment is in-memory only.

Automatic acceptance requires the existing 0.90 score and a minimum 0.08 margin
over the next *distinct recording*. A margin exactly equal to 0.08 is accepted;
close runners-up are `ASK_USER`. Scores are deterministic match scores, not
probabilities.

Recording-version markers (live, remix, remaster, acoustic, instrumental,
radio edit, extended, demo, mono/stereo, sped-up/slowed, explicit/clean, and
the existing vocabulary) are reused from canonical identity normalization.
Requested/candidate version conflicts downgrade automatic acceptance.

Cross-provider copies are clustered ephemerally with the existing pure identity
matcher. Valid matching ISRCs corroborate one recording only when there is no
hard conflict; conflicting ISRC/version/explicit evidence never groups. Without
ISRC, exact normalized title/artist and strict compatible duration are required.
Clustering creates no `Track` or `TrackSource` rows, performs no discovery, and
does not choose a download provider. A cluster representative is selected by
rank/order, never provider priority.

## Confirmation and security

Every ACCEPT and ASK_USER result still requires explicit Download. ACCEPT keeps
alternatives hidden behind **Choose another**; ASK_USER may show up to three.
Selecting an alternative changes only the owned ephemeral confirmation. **None
of these** discards it and returns to localized SEARCH_INPUT; REJECT does the
same retry guidance. Existing opaque `dl18` callbacks retain ownership,
context, expiry, validation, and the Telegram 64-byte limit. No canonical
resolution or durable submission occurs before Download.

The post-confirmation path remains
`RecognizedTrackResolutionAdapter → ResolveTrackService → canonical Track →
durable submission → ProviderResolver/QualityResolver`. Thus a Spotify search
result may resolve to another verified provider for the requested quality.

## Acceptance matrix

| Area | Required behavior |
| --- | --- |
| Thresholds | 0.90 accept, 0.60 ask, 0.08 distinct-recording margin |
| Variants | mismatch cannot silently accept; matching variant follows normal policy |
| Enrichment | max five, deterministic, failure-isolated, identity-validated |
| Corroboration | ephemeral, conservative, no provider boost or persistence |
| UX | explicit Download; correction; None of these; localized retry |
| Boundary | canonical/queue work only after Download |

## Non-goals

No new provider, authorization flow, database migration, dependency, audio
fingerprinting, embeddings, LLM recognition, external recognition service, or
download-pipeline rewrite is part of Stage 29. Apple Music, Qobuz, and YouTube
Music integrations remain future work (Stage 30).

See also [Stage 17](stage17-track-recognition.md) and
[Stage 18](stage18-download-flow.md) for the historical foundations.
