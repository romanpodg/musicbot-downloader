# Stage 18 — Download Flow UX

Stage 18 connects a confirmed Stage 17 catalog match to the accepted download and Telegram delivery path. It adds no downloader, queue, provider runtime, provider authorization flow, storage system, or persistent job model.

## Existing architecture reused

```text
Stage 17 RecognitionResult (ephemeral catalog Track)
  -> Stage 18 confirmation
  -> ResolveTrackService.resolve_provider_track (canonical Track + verified TrackSource)
  -> TelegramDeliveryRequest (Stage 9 durable delivery admission)
  -> TelegramDeliveryWorker / DeliveryPreparationService
  -> Stage 7.1 SingleFlight -> existing DownloadJob
  -> Stage 6 DownloadPipeline -> existing UploadJob
  -> DeliveryService / Telegram cache upload
  -> existing TelegramDeliveryWorker cached-file fanout -> user chat
```

`DownloadJob` retains `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, and `CANCELLED`. The Stage 7 queue manager claims it through the existing `DownloadWorkerBackend`; that worker invokes Stage 6, validates the temporary artifact, and hands it to the existing upload queue. No Stage 18 use case or UX service calls provider download methods, FFmpeg, or Telegram APIs.

The catalog `Track` returned by Stage 17 is deliberately not a canonical persisted Track. On confirmation, `RecognizedTrackResolutionAdapter` delegates to `ResolveTrackService.resolve_provider_track()`. This gets authoritative metadata through the isolated provider boundary and persists or looks up the canonical Track before queue admission. It does not fabricate a provider URL and preserves Stage 3 matching and Stage 4–6 planning.

## Download request and confirmation

`app.core.download.DownloadRequest` is an immutable intent value: `DownloadRequest(user_id, recognized_track, DownloadOptions)`. It has no provider, Telegram, queue, or file-system behavior. `CancelDownloadRequest` is prepared for a later cancellation UI only; existing subscriber and job cancellation remain Stage 7.1-owned. `MetadataProcessor` is a protocol extension point only: no tagging, cover, filename, or ID3 work is registered in this stage.

`DownloadTrackUseCase` resolves the selected catalog identity through an injected canonical-track port and hands its ID to an injected durable-submission port. `DownloadService` owns only short-lived, opaque, user-bound confirmation contexts. They are not persistent jobs; after a restart an unconfirmed callback is invalid. Callback data contains an opaque 24-character token and a bounded alternative index, never provider IDs, credentials, paths, or errors.

`ExistingDeliverySubmissionService` reuses `TelegramTrackRequestService` to create the durable Stage 9 request. With a saved quality it starts that existing action immediately, causing the normal delivery worker to reach the existing queue. With no saved quality it returns `AWAITING_QUALITY` and renders the existing first-quality picker; that explicit choice queues the request. There are no silent downloads.

## Telegram UX and states

Every non-rejected recognition, including high confidence, renders `Found track: Artist — Title` with `[Download] [Cancel]`. Medium-confidence alternatives are listed and can be selected before Download. The UX foundation now has `DOWNLOAD_CONFIRMATION`, `DOWNLOAD_QUEUED`, `DOWNLOAD_PROCESSING`, `DOWNLOAD_COMPLETED`, and `DOWNLOAD_FAILED`. Persistent queue and delivery state remains in existing SQLite models.

`DownloadProgressService` translates actual durable `TelegramDeliveryStatus` and, when available, `QueueJobStatus` snapshots. It has no timers or synthetic progress. The current UX shows the real queued preparation state and the existing Stage 9 fanout sends the final cached file. A future presentation-only status editor can consume the same translator without changing workers or queues.

All Stage 18 failures pass through `UxErrorService` to the localized generic message `❌ Download failed. Please try again.` Exception text, provider diagnostics, file paths, and stack traces never reach Telegram.

## Delivery separation

`DeliveryService` in `app.services.telegram_upload` receives the completed Stage 6 artifact through the existing Stage 7 `UploadRequest`. It validates artifact metadata and size, creates or reuses the bot-scoped Telegram cache entry through the existing gateway, and returns the cache result. `TelegramCacheUploadExecutor` remains the Stage 7 compatibility adapter, while the Stage 9 delivery worker sends the cached file to the user. Download, queue, storage, cache upload, and user delivery therefore remain replaceable boundaries.

## Validation

Unit coverage includes request validation, confirmation ownership and alternatives, use-case delegation, strict callbacks, UX transitions, backend-state/error normalization, the metadata protocol, and `DeliveryService` failure guarding. The integration test uses an in-process fake pipeline and Telegram gateway to prove `recognized Track -> confirmation -> DownloadRequest -> existing delivery admission -> existing SingleFlight DownloadJob -> existing download worker -> existing upload/cache delivery -> existing Telegram delivery worker -> delivered file`. No external provider or Telegram account is required.
