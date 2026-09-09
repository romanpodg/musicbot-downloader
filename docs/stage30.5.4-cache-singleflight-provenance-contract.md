# Stage 30.5.4 — Cache / SingleFlight Provenance Contract Hardening

Stage 30.5.4 documents and regression-tests the existing three-identity contract. It does not
change cache keys, schema, migrations, processing versions, provider ordering, or account lifecycle.

## Three identities

1. **Admission/request identity** is the provider representation through which a durable request
   entered the lifecycle. `DownloadRequestRecord.provider` and `provider_media_id`, and the Stage 24
   `TelegramCacheKey.provider` and `provider_media_id`, retain this identity. It is not a claim about
   which provider produced bytes.
2. **Actual acquisition provenance** is the provider representation that successfully completed
   execution. `DownloadResult.provider` / `provider_track_id` are copied to
   `DownloadArtifactMetadata`, `UploadJob`, and the Stage 8 `TelegramFileCache.source_provider` /
   `source_provider_track_id`. The successful `ProviderAttempt` also records its account ID; that
   operational account provenance deliberately does not propagate to artifact or cache metadata.
3. **Technical artifact identity** describes the requested output contract, not the acquisition
   source. `MediaArtifactSpec` fingerprints effective quality and format, metadata and cover facts,
   codec parameters, and processing version. Durable SingleFlight is keyed by `track_id`,
   `quality_profile`, and `artifact_fingerprint`; provider is intentionally excluded. Stage 8 technical
   reuse follows that same provider-neutral output identity.

For example, an Apple-admitted `AAC_256` request may fail Apple acquisition and complete via a Qobuz
FLAC-to-AAC transcode. Its Stage 24 key remains **Apple** because that is the request admission; its
artifact metadata, upload job, Stage 8 cache row, and successful provider attempt say **Qobuz**
because Qobuz actually acquired the bytes. Those facts are complementary, not contradictory.

## Cache and concurrency behavior

Different Apple and Qobuz admissions for the same canonical track retain distinct Stage 24 keys when
their admitted provider/media identities differ. They can still share a Stage 8 artifact and one
SingleFlight job when their provider-neutral artifact fingerprint is identical. Subscribers receive the
one completed technical artifact and its one actual acquisition history; admission does not promise
provider-specific bytes.

Provider reset or current provider-health state changes future acquisition eligibility only. It does
not invalidate an already valid Stage 8 Telegram reference. Existing invalid-file handling remains the
authority that invalidates a cache reference after Telegram reports it invalid.

`TelegramCacheKey.delivery_mode` is persisted on the Stage 24 row but intentionally omitted from its
current serialized fingerprint. It is a delivery presentation choice, not an input to the Stage 8
uploaded artifact: upload media kind is derived from the output container. On a Stage 24 hit, the
delivery worker selects the cached-audio or cached-document API from the requesting lifecycle
record's delivery mode. This stage characterizes that established behavior without changing persistent
identity or cache versioning. `artwork_identity` remains `None` for the current request construction;
no provider artwork identity is inferred by this stage.

The Stage 25 executor still reads `latest_request_for_track()` for an active shared job. The regression
characterizes the safe case: admissions with the same canonical technical-output contract coalesce and
the shared result satisfies that contract even when its actual provenance is a fallback provider.
