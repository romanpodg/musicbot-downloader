# Stage 26 — Metadata & Media Processing

## 1. Goal

Produce the final user-requested audio artifact (container/codec, truthful quality,
metadata, optional artwork, and safe filename) after Stage 25 has selected and
downloaded a provider source.  The result is handed to the existing upload,
Telegram cache, delivery, retry, and recovery lifecycle.

This document is the authoritative Stage 26 contract.  It is an audit/specification
only; it does not claim Stage 26 is implemented.

## 2. Scope and non-goals

Stage 26 owns transformation and validation of one already downloaded artifact.
It may extend the existing `DownloadPipeline`/`MediaProbe`/`Transcoder` boundary,
but it does not own provider discovery, fallback, queues, lifecycle state,
SingleFlight, Telegram delivery, collection expansion, or cache policy.

Out of scope: a persistent artist/album library, directories or ZIP archives,
playlist synchronization, history deletion/UI redesign, new providers, provider
preference UI, recognition redesign, recommendations, global concurrency,
quotas/rate limits, `/system` redesign, and polished progress UX.

## 3. Current production authority

The running composition in `app/composition.py::compose_stage9` wires:

* `Stage25DownloadExecutor` -> `DownloadPipeline.download_selected()`;
* `DownloadPipeline` -> `MediaProbe` and `Transcoder`;
* `DownloadArtifactManager` owns per-job temporary roots;
* `DownloadWorkerBackend` validates/handoffs a result to the durable `UploadJob`;
* `UploadWorkerBackend` invokes `TelegramCacheUploadExecutor`;
* `DeliveryService` chooses Telegram audio/document upload and a sanitized display filename;
* `TelegramDeliveryWorker` performs cache-first fan-out and lifecycle reconciliation.

Target call graph:

```text
Stage25DownloadExecutor
  -> DownloadPipeline.download_selected()
  -> provider native artifact (attempt workspace)
  -> Stage 26 processor (format/tags/artwork/filename)
  -> MediaProbe + artifact validator
  -> DownloadWorkerBackend -> UploadJob
  -> UploadWorkerBackend -> TelegramCacheUploadExecutor
  -> Telegram file cache / Stage 24 artifact cache
  -> TelegramDeliveryWorker (AUDIO or DOCUMENT)
```

## 4. Authority map

| Decision | Current authority | Target authority | Duplicate/legacy authority |
|---|---|---|---|
| Transcoding | `DownloadPipeline._execute_plan` + `Transcoder.transcode` | One Stage 26 processor called by the pipeline | Stage 5 `QualityResolver` only plans quality; it must not process media |
| Container/codec/bitrate | `QUALITY_OUTPUTS` and `Transcoder.command` | Frozen effective profile translated to an explicit media output spec | Provider OnTheSpot finalizer is bypassed; developer `app.tools.download` is compatibility-only |
| Metadata | `_track_metadata` and four FFmpeg `-metadata` keys | Stage 26 metadata mapper from frozen canonical/collection snapshot | Provider metadata JSON and search results are not tag authorities |
| Artwork | None | Stage 26 artwork processor | No production artwork path exists |
| Filename | `telegram_upload.sanitize_display_filename` at upload | Stage 26 artifact extension plus existing Telegram sanitizer | No persistent library naming path |
| Validation | `MediaProbe` and `output_satisfies_specification` | Same boundary, extended for profile/tags/artwork invariants | Upload worker only repeats existence/size/path checks |
| Ownership/cleanup | `DownloadArtifactManager`, upload queue, stale cleanup | Existing managers; Stage 26 must not add a second owner | `DownloadWorkspaceManager` is a separate compatibility helper |

## 5. Format semantics

`FormatPreference` already represents `ORIGINAL`, `FLAC`, `MP3`, and `M4A`, but
the technical queue contract carries only `QualityProfile`.  The admitted Stage
22/24 `EffectiveDownloadProfile` is persisted on `download_requests`, yet the
production worker passes only `job.quality_profile` to Stage 25/6.  Therefore the
format preference is currently not authoritative at execution time.

* **ORIGINAL:** currently means only the quality planner's native/direct result;
  it is not a general preserve-source mode.  A native FLAC can be delivered for
  `LOSSLESS`; native MP3/AAC plans are selected by quality, not by `ORIGINAL`.
  No explicit container-preservation contract, metadata/artwork policy, or
  extension check exists for arbitrary provider artifacts.
* **FLAC:** no Stage 26 FLAC conversion exists.  FLAC output is possible only
  when a provider supplies a genuine native FLAC source and the quality plan is
  `LOSSLESS`; lossy-to-FLAC is forbidden by the quality policy.  This is not
  user format wiring.
* **MP3:** `Transcoder` produces `libmp3lame` MP3 at 128 or 320 kbps from a
  genuine lossless source; direct exact native MP3 is also supported.  The
  bitrate is authoritative in `QUALITY_OUTPUTS`, not a media-code hard-code
  (the encoder/muxer selection is hard-coded).
* **M4A:** `Transcoder` writes an `ipod`/MP4-family container with the native
  FFmpeg `aac` encoder at 256 kbps.  “M4A” is a container preference, not a
  promise of AAC in the current preference path; AAC is used by the
  `AAC_256` quality profile.  No preference-to-runtime wiring exists.

The processor must preserve source sample rate/channels/bit depth unless a
profile intentionally changes them, and must never describe a lossy source
transcoded to FLAC as lossless.  Container, codec, extension, and MIME must be
derived from the actual validated output.

## 6. Metadata contract

The canonical storage `Track` currently has title, artist, album, release date,
ISRC, and explicit flag.  `DownloadPipeline._track_metadata` emits only
`title`, `artist`, `album`, and `isrc`.  `Transcoder` and `tag_copy` write only
those four keys.  Album snapshots add title/artist/release date and per-item
position, but child persistence does not retain track/disc totals or numbers.

| Field | Source/current model | FLAC | MP3 | M4A | Status |
|---|---|---|---|---|---|
| TITLE | canonical `Track.title` | `title` | ID3 title | `title` atom | PARTIAL |
| ARTIST | canonical `Track.artist` | `artist` | ID3 artist | `artist` atom | PARTIAL |
| ALBUM | canonical `Track.album` | `album` | ID3 album | `album` atom | PARTIAL |
| ALBUMARTIST | none | absent | absent | absent | MISSING |
| TRACKNUMBER | none for single tracks; collection position is not persisted into child tags | absent | absent | absent | MISSING |
| DISCNUMBER | none | absent | absent | absent | MISSING |
| DATE | `Track.release_date` exists but is not emitted | absent | absent | absent | MISSING |
| ISRC | canonical `Track.isrc` | `isrc` | `TSRC` convention not explicitly mapped | `©isrc`/atom convention not explicitly mapped | PARTIAL |
| COPYRIGHT | none | absent | absent | absent | MISSING |
| Explicit flag | canonical `Track.explicit` exists | no container mapping | no container mapping | no container mapping | MISSING |

Missing values must be omitted, never invented.  Future precedence is:
frozen canonical track identity, then immutable Stage 23 item/collection snapshot
for collection-only fields, with provider metadata only as an already-admitted
source snapshot.  The processor must not search or resolve providers.

## 7. Artwork contract

No production image fetch, validation, resize/re-encode, or embedding path was
found.  `embed_cover` is persisted in Stage 22/24 profiles and in the Stage 24
artifact-cache key, but is not consumed by `DownloadPipeline`, `Transcoder`, or
`DeliveryService`.  `cover=true` therefore does not embed artwork and
`cover=false` has no processing effect.  Existing artwork in an ORIGINAL/native
artifact is preserved only accidentally when a direct file is moved; it is not
controlled or validated.  Missing artwork must remain non-fatal in the target
contract and must never trigger Stage 25 fallback.

Stage 26 must define bounded artwork bytes, supported image types, optional
re-encoding, and per-container embedding (FLAC picture block, MP3 APIC, and
M4A cover atom) before implementation.

## 8. Filename and delivery contract

`DeliveryService` supplies Telegram with a sanitized `Artist - Title.ext`
filename, normalizing Unicode, replacing Windows/POSIX unsafe characters,
collapsing whitespace, trimming dots/spaces, bounding length to 180, and using
`track.bin` when both fields are empty.  The extension is derived from the
validated output container and Telegram uses it for both `send_audio` and
`send_document`.  Unit/integration coverage proves the sanitizer and upload
filename behavior.  Album ordinal prefixes are not currently implemented.
`TelegramUploadSpec` has no MIME/content-type field; aiogram/Telegram infer it
from the filename and media method, while the artifact-cache MIME column is not
populated by the current delivery recorder.  Deterministic MIME mapping is
therefore a Stage 26 gap.

Persistent artist/album directory hierarchy has no observable purpose in the
temporary-artifact/Telegram product and is OUT OF SCOPE.

## 9. Preference, cache, and SingleFlight identity

Stage 22 resolves and Stage 21 freezes quality, format, delivery mode, metadata,
and cover on `download_requests`.  `DownloadRequestRecord.effective_profile`
reconstructs that snapshot without reading live settings, but the technical
`DownloadJob`, `UploadJob`, and `DownloadFlight` keys contain only
`track_id + quality_profile`.  `DownloadWorkerBackend` and
`Stage25DownloadExecutor` consequently cannot honor a per-request format or
metadata/cover profile.

The Stage 24 `TelegramCacheKey` includes provider/media ID, effective quality,
effective format, delivery mode, metadata/cover flags, and processing version,
but the real delivery path first consults the older `telegram_file_cache` keyed
only by bot/track/quality.  Byte-different requests can therefore collide before
the artifact-exact cache is consulted.  SingleFlight has the same collision
surface.  This is a correctness blocker, not merely missing UI.

## 10. Errors, retries, and ownership

`MediaProbe` validates owned, non-empty, non-partial files, requires an audio
stream, and records codec/container/bitrate/sample-rate/channels/duration.
`output_satisfies_specification` checks file existence, duration tolerance,
container/codec, bitrate tolerance, and lossless provenance.  FFmpeg exit status
alone is not accepted.  Processing errors are typed as `TRANSCODE_FAILED`,
`OUTPUT_VALIDATION_FAILED`, or probe errors; Stage 25's fallback policy excludes
these from provider fallback.  They are not in the download worker's retryable
set, so they are terminal for the technical job.  The target contract should
surface a durable `PROCESSING` classification while retaining this no-provider-
fallback rule.

The provider artifact is placed under a per-job attempt directory.  Successful
output is handed to `UploadJob`; the upload queue releases the root after a
terminal/successful upload, and stale unowned roots are removed by
`StaleArtifactCleanupService`.  Pipeline exceptions release the root and clean
attempts.  No separate Stage 26 ownership or retry system is allowed.

## 11. Collection behavior

Stage 23 snapshots are immutable and children use the normal Track admission,
Stage 25 resolution, SingleFlight, upload, and cache lifecycle.  Album snapshots
retain ordered item position, title/artist, and collection release date, but
`BatchDownloadItem` does not persist track number, disc number, totals, album
artist, ISRC, or explicit values.  Playlist position must never be emitted as
album `TRACKNUMBER`; actual track metadata remains authoritative.

## 12. Acceptance matrix

| Requirement | Status | Current authority | Production wired? | Evidence | Gap |
|---|---|---|---|---|---|
| ORIGINAL | PARTIAL | Quality planner + direct pipeline move | Partly | `DownloadPipeline._execute_plan`; media tests | No general preserve-source/effective-format semantics |
| FLAC | MISSING | Native FLAC + LOSSLESS quality only | No preference path | `QUALITY_OUTPUTS`; ffprobe container tests | Add explicit FLAC processing/profile wiring |
| MP3 | PARTIAL | `QUALITY_OUTPUTS` + `Transcoder` | Yes for quality profiles | 19 container-image media tests; `test_media_processing.py` | Format preference/metadata/artwork not wired |
| M4A | PARTIAL | AAC `ipod` muxer in `Transcoder` | Yes for AAC_256 | Container probe result below | M4A preference and atom contract absent |
| TITLE | PARTIAL | `Track.title` -> FFmpeg metadata | Yes | pipeline code/tests | Profile gating and full tag mapping |
| ARTIST | PARTIAL | `Track.artist` | Yes | pipeline/upload tests | Same |
| ALBUM | PARTIAL | `Track.album` | Yes when present | pipeline code | Same |
| ALBUMARTIST | MISSING | None | No | model search | Add source/mapping |
| TRACKNUMBER | MISSING | None persisted for children | No | Stage 23 models | Persist/derive immutable item number |
| DISCNUMBER | MISSING | None | No | Stage 23 models | Persist/derive disc number |
| DATE | MISSING | `Track.release_date` unused | No | storage model | Emit date mapping |
| ISRC | PARTIAL | `Track.isrc` -> generic `isrc` | Yes | media command tests | Container-specific mapping/proof |
| COPYRIGHT | MISSING | None | No | model search | Add only if authoritative source exists |
| Explicit flag | MISSING | `Track.explicit` unused by media | No | model search | Define per-container convention |
| Metadata preference | MISSING | Frozen profile only | No | Stage 22 fields; pipeline ignores | Gate optional injection; preserve original tags policy |
| Cover preference | MISSING | Frozen profile only | No | no artwork module | Add bounded artwork path |
| FLAC artwork | MISSING | None | No | no image code | Embed/validate picture block |
| MP3 artwork | MISSING | None | No | no image code | Embed APIC |
| M4A artwork | MISSING | None | No | no image code | Embed cover atom |
| Filename sanitization | PASS | `DeliveryService.sanitize_display_filename` | Yes | unit + integration upload tests | Optional album ordinal convention |
| Extension/container consistency | PARTIAL | `_output_extension` + Telegram `_extension` | Partly | ffprobe output and pipeline validation | ORIGINAL arbitrary-container agreement; MIME is inferred, not explicit |
| Artifact validation | PASS | `MediaProbe` + `output_satisfies_specification` | Yes | media unit/integration tests | Add tag/artwork assertions |
| Processing-error classification | PARTIAL | typed media errors + Stage 25 no-fallback | Yes | `provider_fallback.py`, worker retry set | Normalize durable PROCESSING classification |
| Artifact cleanup | PASS | `DownloadArtifactManager` + stale cleanup | Yes | pipeline cleanup tests | Add processed-intermediate cases |
| Frozen effective profile | PARTIAL | Stage 21 request snapshot | Snapshot persisted, not consumed by worker | lifecycle/profile tests | Carry profile to processing boundary |
| Telegram cache identity | MISSING | Stage 24 key, but Stage 8 technical cache first | No | `TelegramCacheKey` vs `telegram_file_cache` unique key | Prevent profile collisions |
| SingleFlight identity | MISSING | `DownloadFlight(track_id, quality_profile)` | Yes, unsafe | queue model + SingleFlight tests | Profile-aware key or safe post-convergence processing |
| Collection metadata | PARTIAL | Stage 23 immutable snapshot | Partly | batch models/tests | Persist required album/item fields |
| Production codec availability | PASS | Production Docker image FFmpeg | Yes | container encoder/probe run | Keep image gate in acceptance |

## 13. Schema decision

**MIGRATION REQUIRED.**  Stage 21 request rows can represent the frozen format,
metadata, cover, and delivery profile, and Stage 24 has an artifact-exact cache
table.  However, the durable technical queue/flight and legacy Telegram cache
uniqueness constraints cannot distinguish byte-different profiles.  The minimal
schema invariant is a durable profile fingerprint (or equivalent format,
metadata, cover, and delivery columns) on the technical job/flight/cache key,
with backfill rules for legacy rows.  Do not create that migration in the audit.

## 14. Minimal implementation plan

1. Extend `DownloadWorkerBackend`/`Stage25DownloadExecutor` and the authoritative
   pipeline seam to pass the admitted `EffectiveDownloadProfile`; add the
   smallest durable queue/flight/cache key migration.  Prove settings changes
   after admission cannot alter output and profile variants cannot collide.
2. Extend `DownloadPipeline`/`Transcoder` with one Stage 26 processor that maps
   ORIGINAL/FLAC/MP3/M4A to explicit container/codec/bitrate behavior, gates
   metadata, and validates extension/container/quality.  Add synthetic ffprobe
   tests for all four modes and lossy-quality truthfulness.
3. Add metadata mapping from canonical Track plus immutable collection fields,
   with FLAC Vorbis, MP3 ID3, and M4A atom tests for missing values and explicit
   conventions.  No provider lookup is allowed.
4. Add bounded artwork fetch/validation/embedding in that same processor and
   prove unavailable optional artwork is non-fatal for FLAC/MP3/M4A.
5. Keep `DeliveryService` as the filename/MIME/upload authority, extending only
   where final validated container/profile data is needed; test AUDIO and
   DOCUMENT with the same processed artifact.
6. Normalize processing failures to durable `PROCESSING` semantics while
   preserving existing Stage 21 retries and Stage 25 no-fallback policy; add
   cleanup tests for every failure boundary.

## 15. Required Stage 26 acceptance tests

* Frozen-profile admission followed by preference mutation still yields the
  admitted format/metadata/cover output.
* ORIGINAL preserves a valid native artifact and never lies about its container
  or quality.
* Native/lossless FLAC, MP3 128/320, and AAC-in-M4A 256 synthetic fixtures pass
  ffprobe container/codec/sample-rate/channel/bitrate/duration checks.
* Lossy input cannot be accepted as lossless FLAC.
* Every metadata field and missing-data rule is tested for FLAC, MP3, and M4A.
* Artwork true/false, unavailable artwork, invalid image bytes, and all three
  container embedding paths are tested.
* Filename sanitization covers Unicode, separators, control characters, empty
  fields, long names, and extension agreement.
* Profile variants do not collide in Telegram cache or SingleFlight; identical
  profiles converge safely.
* FFmpeg/ffprobe failures, corrupt output, duration mismatch, cancellation,
  replacement failure, upload failure, restart recovery, and stale cleanup are
  classified and cleaned through existing lifecycle paths.
* Production-derived Linux image probes prove FLAC, libmp3lame, AAC, and image
  support; host-only results are not accepted as container evidence.

## 16. Regression boundaries

Preserve Stage 21 durable lifecycle and retry/recovery state; Stage 22 effective
profile resolution; Stage 23 immutable collection snapshots and child lifecycle;
Stage 24 history replay and cache-first behavior; Stage 25 candidate/account
resolution and fallback; SingleFlight; OnTheSpot isolation; UploadQueue/
UploadWorker; Telegram delivery routing; and artifact ownership/idempotency.

## 17. Required production-level proofs

Run focused unit/integration media, pipeline, profile, cache, and SingleFlight
tests; run `git diff --check`; and run the production-derived validation image
with real FFmpeg/ffprobe encoder and synthetic fixture probes.  Do not claim
Linux/container acceptance from a developer host.

## 18. Definition of done

All format modes and frozen profile fields reach one authoritative processor;
final artifacts are ffprobe-validated with truthful quality; required metadata
and optional artwork are mapped per container; filenames/extensions/MIME agree;
profile-aware cache and SingleFlight identities cannot reuse byte-different
artifacts; processing failures remain local and cleanly owned; collection
metadata is immutable and accurate; focused tests and production-image probes
pass; and all regression boundaries remain intact.

## 19. Audit evidence captured

Host focused checks: `31 passed, 1 skipped` for media, pipeline, cache, and
preference tests when run with repository-local pytest temp roots.  The host had
no `ffmpeg`/`ffprobe` executables.  The existing production-derived validation
image ran `19 passed` for media/FFmpeg tests.  Its FFmpeg encoder list included
`flac`, `libmp3lame`, and `aac`; synthetic probes produced FLAC, MP3/libmp3lame,
and MP4/M4A/AAC artifacts with 44.1 kHz audio and expected durations.

The repository `scripts/validate-production.sh` could not run directly because
WSL's `/bin/bash` is unavailable in this host environment; this is an
environment blocker, not a production-code result.

## 20. Verdict

**STAGE 26 SPECIFICATION/AUDIT BLOCKED**

The contract is ready, but implementation is blocked until the durable
profile-aware technical cache/SingleFlight boundary and the single Stage 26
processor are implemented.  Priority order is: (1) profile propagation and
identity migration, (2) format processor and validation, (3) metadata, (4)
artwork, (5) failure normalization and acceptance proofs.
