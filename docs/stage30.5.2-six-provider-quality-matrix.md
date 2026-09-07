# Stage 30.5.2 — Six-Provider Quality Matrix Regression

This stage makes the existing provider-neutral quality policy explicit as a
deterministic regression contract. It does not change provider resolution,
ranking, accounts, caches, recognition, the OnTheSpot pin, or provider
activation.

## Application profiles and authority

The only exact application profiles are `AAC_128`, `AAC_256`, `MP3_128`,
`MP3_320`, and `LOSSLESS`. `app.core.quality.QualityResolver` creates the safe
plans and `plan_sort_key()` remains the sole semantic ordering authority:
confirmed direct, preflight direct, confirmed lossless-to-lossy transcode, then
preflight transcode. Stage 25 consumes that globally ordered plan set before
recognition affinity or provider priority.

## Capability before evidence versus exact media after evidence

Provider capability is not proof of an individual artifact. A declared media
format creates a plan requiring preflight until the source is inspected.
Observed native media produces a confirmed plan only for an exact lossy
codec/bitrate match or genuine native FLAC.

| Provider | Capability / verified native truth | AAC_128 | AAC_256 | MP3_128 | MP3_320 | LOSSLESS |
| --- | --- | --- | --- | --- | --- | --- |
| YouTube Music | AAC/M4A 128 | direct | — | — | — | — |
| Apple Music | AAC/M4A 256 | — | direct | — | — | — |
| Qobuz | native FLAC | transcode | transcode | transcode | transcode | direct |
| Spotify | Vorbis/Ogg lossy | — | — | — | — | — |

Deezer declares variable MP3 (128/256/320) or FLAC media; Tidal declares AAC/M4A
or FLAC without an exact declared AAC bitrate. Their preflight plans therefore
remain `REQUIRES_PREFLIGHT`. Once actual evidence is available, each uses the
same policy as every other provider: FLAC is direct for `LOSSLESS` and can be
application-transcoded to any lossy application profile; lossy media is direct
only for its exact application codec/bitrate pair.

## Safety and provenance

There is no lossy-to-lossy conversion, lossy-to-lossless conversion, or bitrate
upscale. Source/output bitrate tolerances validate nominal probed media; they do
not authorize a plan or conversion. Provider decryption is separate from quality
transcoding: an Apple AAC256 direct artifact may be provider-decrypted but is
not application-transcoded; Qobuz FLAC is direct for `LOSSLESS` and is marked
application-transcoded only when producing a lossy output.

The deterministic FFmpeg/ffprobe suite validates the five final output
contracts using the shared generic tolerance. External provider smoke and
container validation are intentionally outside this regression stage.

## Non-goals

No new quality profile, provider-specific tolerance, provider ordering change,
registry change, authorization work, migration, dependency, or Bandcamp/
SoundCloud activation is included.
