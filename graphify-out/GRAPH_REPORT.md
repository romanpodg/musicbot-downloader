# Graph Report - musicbot-downloader  (2026-09-05)

## Corpus Check
- 369 files · ~217,323 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 4842 nodes · 18322 edges · 196 communities (136 shown, 16 thin omitted)
- Extraction: 84% EXTRACTED · 16% INFERRED · 0% AMBIGUOUS · INFERRED: 2934 edges (avg confidence: 0.95)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `30d8a6ec`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- unit/test_stage18_download.py
- Track
- workers.py
- TelegramUploadReceipt
- test_stage25_worker_execution.py
- track_recognition.py
- ProviderAccountErrorCode
- integration/test_stage18_download.py
- services/download_lifecycle.py
- enums.py
- ProviderAuthorizationOutcome
- TelegramContext
- MusicProviderName
- ProviderUnavailable
- integration/test_stage15_search.py
- SingleFlightService
- media.py
- unit/test_stage104_provider_health.py
- TelegramDeliveryStatus
- ProviderRuntimeStatus
- RuntimeWorkerControlService
- QueueErrorCode
- TelegramAuthorizationService
- models/base.py
- Database
- ux_handlers.py
- test_stage27_production_hardening.py
- Settings
- DeepLinkRegistryService
- test_stage134_spotify_authorization.py
- TelegramUserService
- UploadJob
- RuntimeError
- repositories/__init__.py
- SubscriberLifecycleNotifier
- handlers.py
- composition.py
- NativeCodec
- DownloadArtifactManager
- QualityProfile
- ProviderRateLimitSnapshot
- TelegramArtifactCacheService
- test_stage93_albums.py
- test_stage93_album_integration.py
- test_stage133_deezer_authorization.py
- OnTheSpotWorker
- test_stage9_telegram.py
- OnTheSpotProvider
- test_stage103_runtime_worker_controls.py
- utc_now
- LocalizationService
- OperationalAuditService
- AdminManagementPresentation
- test_stage28_collection_presentation.py
- worker.py
- services/singleflight.py
- TelegramPresentation
- PreparedSourceMedia
- ProviderAccountStatus
- logging.py
- ProviderAuthorizationMethod
- _worker_with_tidal
- _worker_with_deezer
- database.py
- BatchDownloadRepository
- User
- ResolveTrackService
- ops.py
- ProviderAccountManagementService
- test_onthespot_provider.py
- repositories/singleflight.py
- test_stage9_callbacks.py
- What You Must Do When Invoked
- test_stage134_spotify_telegram.py
- ProviderResolutionRepository
- admin_handlers.py
- exceptions.py
- TelegramAlbumRequestService
- models.py
- DownloadLifecycleService
- test_onthespot_worker.py
- TrackReference
- .spotify_webapi_authorize
- integration/test_stage132_tidal_authorization.py
- Stage 26 — Metadata & Media Processing
- test_config.py
- Any
- Stage9Components
- Musicbot Downloader
- test_stage133_deezer_telegram.py
- _pinned_worker
- UserRole
- test_stage123_ops.py
- test_stage11_telegram_deep_links.py
- TelegramFileCache
- _AtomicReplaceTextFile
- .quality_profile
- queues.py
- README.md
- CallbackQuery
- ._close_spotify_playback_pairing
- AsyncWorkerPool
- InternalApiServer
- .tidal_device_authorization_poll
- Message
- FakeAiogramBot
- test_stage24_history_cache.py
- Production deployment and release validation (Stage 12.4)
- Downloader release checklist
- Stage 11 Internal API and Deep-Link Registry
- test_migrations.py
- Stage 15 — Track Search Architecture
- OnTheSpot v1.8.1 provider capability matrix
- .create_from_collection
- Stage 29 — Recognition 2.0
- graphify reference: extra exports and benchmark
- Stage 27 — Production Limits, Resource Safety & Observability
- DownloadWorkspaceManager
- Stage 20 — Architecture & Production Review
- Stage 7 queue lifecycle
- Stage 8 Telegram cache and delivery foundation
- Stage 13.3 — Deezer ARL Authorization
- Stage 13.4 — Spotify Playback and Search Credentials
- Stage 13.5 — Provider Lifecycle, Security, and Crash Hardening
- Stage 14 — Telegram Bot UX Foundation
- Stage 17 — Track Recognition
- Stage 18 — Download Flow UX
- Stage 19 — Channel/Bot Integration
- 20260817_0003_track_identity_keys.py
- graphify reference: query, path, explain
- test_onthespot_external.py
- Stage 28 — Telegram UX & Interaction Consistency
- validate-production.sh
- graphify reference: add a URL and watch a folder
- graphify reference: commit hook and native CLAUDE.md integration
- graphify reference: incremental update and cluster-only
- graphify reference: GitHub clone and cross-repo merge
- graphify reference: transcribe video and audio
- application/__init__.py
- core/__init__.py
- app/__init__.py
- providers/__init__.py
- app/services/__init__.py
- tools/__init__.py
- extraction-spec.md
- stage21-download-lifecycle.md
- stage22-download-preferences.md
- stage23-batch-downloads.md
- stage24-history-cache.md
- stage25-provider-resolution.md
- integration/__init__.py
- musicbot-downloader

## God Nodes (most connected - your core abstractions)
1. `MusicProviderName` - 550 edges
2. `Database` - 304 edges
3. `QualityProfile` - 303 edges
4. `LocalizationService` - 117 edges
5. `compose_stage9()` - 113 edges
6. `OnTheSpotProvider` - 106 edges
7. `NativeCodec` - 92 edges
8. `DownloadArtifactManager` - 90 edges
9. `NativeContainer` - 87 edges
10. `OnTheSpotWorker` - 85 edges

## Surprising Connections (you probably didn't know these)
- `test_registry_covers_every_local_music_provider()` --uses--> `MusicProviderName`  [INFERRED]
  tests/unit/test_provider_capabilities.py → app/core/enums.py
- `test_unknown_sample_properties_are_not_reported_as_unsupported()` --uses--> `MusicProviderName`  [INFERRED]
  tests/unit/test_provider_capabilities.py → app/core/enums.py
- `test_child_poll_does_not_call_or_emulate_upstream_blocking_loop()` --uses--> `OnTheSpotWorker`  [INFERRED]
  tests/unit/test_stage132_tidal_authorization.py → app/providers/onthespot/worker.py
- `test_stage14_keyboard_factory_reuses_validated_callbacks()` --uses--> `UxMenu`  [INFERRED]
  tests/unit/test_stage14_ux.py → app/application/ux/flows/navigation.py
- `test_stage14_error_service_hides_internal_error_details()` --uses--> `UxErrorMessage`  [INFERRED]
  tests/unit/test_stage14_ux.py → app/application/ux/services/errors.py

## Import Cycles
- None detected.

## Communities (196 total, 16 thin omitted)

### Community 0 - "unit/test_stage18_download.py"
Cohesion: 0.06
Nodes (56): DownloadService, DownloadSubmissionPort, DownloadTrackUseCase, datetime, Protocol, timedelta, Stage 18 confirmation and download-admission application flow., Store only a pending user choice; rejected recognition never becomes a request. (+48 more)

### Community 1 - "Track"
Cohesion: 0.05
Nodes (73): Stage 15 search and Stage 17 recognition application flow., Search normalized catalogs and optionally recognize the leading intended track., Run Stage 17 after the unchanged Stage 15 search-result boundary., SearchTracksUseCase, Safe UX error normalization; never return exception text to presentation., ProviderAuthenticationError, TrackSearchCandidate, TrackSearchRequest (+65 more)

### Community 2 - "workers.py"
Cohesion: 0.07
Nodes (36): QueueJobStatus, DownloadQueueService, Path, UploadQueueService, DownloadWorkerBackend, QueueManager, Persistent queue processors and dynamically resizable async worker pools., Sweep only safely stale artifacts once before deferring new media work. (+28 more)

### Community 3 - "TelegramUploadReceipt"
Cohesion: 0.05
Nodes (35): TelegramMediaKind, TelegramBotIdentity, TelegramUploadReceipt, telegram_media_kind(), AiogramTelegramGateway, _normalized_error(), Bot, Message (+27 more)

### Community 4 - "test_stage25_worker_execution.py"
Cohesion: 0.05
Nodes (61): ProviderAccountHealthState, ProviderAttemptStatus, candidate_supports_profile(), canonical_identity_from_track(), canonical_identity_from_values(), CanonicalMediaIdentity, match_candidates(), match_media() (+53 more)

### Community 5 - "track_recognition.py"
Cohesion: 0.05
Nodes (94): _normalize_optional_text(), StrEnum, RankedTrackCandidate, Provider-independent domain models for Stage 17 track recognition., A candidate with explainable component scores and its aggregate confidence., The top ranked recognition candidate and any non-selected alternatives., The next interaction required for a ranked recognition candidate., Sanitized, deterministic explanation of a Recognition 2.0 decision. (+86 more)

### Community 6 - "ProviderAccountErrorCode"
Cohesion: 0.08
Nodes (25): ProviderAccountErrorCode, ProviderOperationalState, ProviderAccountBackend, Protocol, _deezer_error_code(), Async lifecycle owner for the isolated OnTheSpot interpreter process., Exception, Protocol (+17 more)

### Community 7 - "integration/test_stage18_download.py"
Cohesion: 0.07
Nodes (57): compose_stage8(), Event, Stage8Components, DeliveryPreparationStatus, TelegramCacheStatus, DeliveryInvariantError, A READY subscriber has no matching active completed-result cache entry., TelegramCacheEntryNotFoundError (+49 more)

### Community 8 - "services/download_lifecycle.py"
Cohesion: 0.09
Nodes (25): DeliveryTargetType, StrEnum, DownloadDeliveryStatus, DownloadSourceType, classify_failure(), LifecycleAdmission, BaseException, datetime (+17 more)

### Community 9 - "enums.py"
Cohesion: 0.09
Nodes (36): default_preferences(), Stage 22 durable preference values and deterministic profile resolution., UserDownloadPreferences, DeliveryMode, FormatPreference, QualityPreference, Provider-independent domain enumerations., Provider-neutral quality defaults exposed to users. (+28 more)

### Community 10 - "ProviderAuthorizationOutcome"
Cohesion: 0.10
Nodes (25): ProviderAuthorizationChallenge, ProviderAuthorizationOutcome, ProviderAuthorizationStartOutcome, ProviderCompoundCredentialInput, ProviderLocalPairingChallenge, Sanitized browser challenge; never contains the device code or credentials., Sanitized local discovery challenge; contains no session or credential data., Opaque Spotify Developer credential pair. (+17 more)

### Community 11 - "TelegramContext"
Cohesion: 0.08
Nodes (47): ChannelTarget, delivery_target_from_values(), DeliveryTarget, GroupChatTarget, A completed artifact destination, independent from the requesting user., ChannelBinding, ChannelBindingStatus, ChatPolicy (+39 more)

### Community 12 - "MusicProviderName"
Cohesion: 0.07
Nodes (42): MusicProviderName, ProviderHealthErrorCode, ProviderHealthStatus, Provider-level readiness, deliberately separate from TrackSource status., ProviderHealthEntry, Sanitized provider-level readiness; never contains account identity or secrets., ProviderAccountComponent, ProviderAccountComponentStatus (+34 more)

### Community 13 - "ProviderUnavailable"
Cohesion: 0.08
Nodes (34): MetadataUnavailable, ProviderOperationTimeout, ProviderUnavailable, A bounded provider operation exceeded its execution timeout., close_shared_process_client(), get_shared_process_client(), OnTheSpotProcessClient, Any (+26 more)

### Community 14 - "integration/test_stage15_search.py"
Cohesion: 0.07
Nodes (50): Coordinates observation and navigation; handlers only render its result., UxFlowService, UxErrorService, In-memory navigation state; durable delivery/card state remains Stage 9-owned., UserUxStateService, encode_ux_callback(), parse_ux_callback(), Versioned, validated callback codec for Stage 14 UX navigation. (+42 more)

### Community 15 - "SingleFlightService"
Cohesion: 0.11
Nodes (33): SubscriberStatus, datetime, Event, Process-local wake-up optimization; SQLite remains authoritative., Normal admission path for shared Track + QualityProfile work.…, SingleFlightService, _subscriber_counts(), SubscriberNotifier (+25 more)

### Community 16 - "media.py"
Cohesion: 0.08
Nodes (45): OutputSpecification, Facts Stage 6 must verify before executing one quality strategy., Exact delivery contract represented by a user QualityProfile., SourceMediaRequirement, ArtworkFetcher, _codec(), _container(), _duration_ms() (+37 more)

### Community 17 - "unit/test_stage104_provider_health.py"
Cohesion: 0.14
Nodes (17): encode_provider_health_callback(), parse_provider_health_callback(), _provider_key(), ProviderHealthCallbackAction, ProviderHealthPresentation, InlineKeyboardMarkup, StrEnum, Localized Provider Health snapshot presentation and strict callback codec. (+9 more)

### Community 18 - "TelegramDeliveryStatus"
Cohesion: 0.10
Nodes (21): Apply a transport-neutral state transition after a confirmed UX action., Stage 14 user-experience application contracts., Exception, StrEnum, Normalize provider-admission failures only at the download UX boundary., UxErrorMessage, UX state, progress, and safe error-normalization services., DownloadProgressService (+13 more)

### Community 19 - "ProviderRuntimeStatus"
Cohesion: 0.07
Nodes (42): ProviderResolutionStatus, ProviderRuntimeStatus, QualityResolutionStatus, The requested canonical Track does not exist., TrackNotFound, ProviderCandidateFailure, ProviderResolutionResult, ProviderSourceCheck (+34 more)

### Community 20 - "RuntimeWorkerControlService"
Cohesion: 0.11
Nodes (24): Protocol, QueueRuntimeSnapshotReader, Authorized runtime controls over the existing Stage 7 worker settings., Change durable desired state; Stage 7 remains the only pool reconciler., RuntimeWorkerControlService, WorkerControlSnapshot, WorkerMutationResult, WorkerPoolType (+16 more)

### Community 21 - "QueueErrorCode"
Cohesion: 0.08
Nodes (28): QueueErrorCode, Delivery failed transiently and may be retried by Stage 7., Delivery failed permanently for this artifact., UploadRetryableError, UploadTerminalError, UploadRequest, UploadResult, MetadataProcessor (+20 more)

### Community 22 - "TelegramAuthorizationService"
Cohesion: 0.06
Nodes (44): ProviderHealthSnapshot, AdminAccessContext, AdminPermission, AuthorizationError, Central, persistence-backed authorization policy for privileged operations., Stage 10.2 owner-only guard foundation., Typed denial with no presentation text or sensitive configuration., Resolve current authority from durable user state and immutable OWNER_ID. (+36 more)

### Community 23 - "models/base.py"
Cohesion: 0.07
Nodes (44): Base, Any, datetime, Declarative base and timestamp conventions., Persist UTC and restore timezone awareness on dialects such as SQLite., TimestampMixin, UTCDateTime, BatchDownloadItem (+36 more)

### Community 24 - "Database"
Cohesion: 0.12
Nodes (25): OwnerBootstrapResult, OwnerBootstrapService, StrEnum, Ensure an observed Telegram user matching OWNER_ID has the OWNER role., Database, test_no_provider_account_credentials_or_pending_sessions_are_persisted(), test_user_observation_preserves_manual_locale_and_reconciles_owner(), asyncio (+17 more)

### Community 25 - "ux_handlers.py"
Cohesion: 0.09
Nodes (41): DownloadConfirmation, Short-lived, user-owned confirmation context; it is never a persistent job…, User interaction flows., _context_for(), StrEnum, Stage 14 navigation flow with no Telegram presentation types., UxMenu, UxScreen (+33 more)

### Community 26 - "test_stage27_production_hardening.py"
Cohesion: 0.08
Nodes (41): BatchSourceType, Provider-neutral immutable member of an album or playlist snapshot., Immutable provider collection membership returned before persistence., ResolvedCollection, ResolvedCollectionItem, BatchDownloadService, CollectionResolver, datetime (+33 more)

### Community 27 - "Settings"
Cohesion: 0.08
Nodes (27): Global download capacity, authoritatively bounded by worker max., Global upload capacity, authoritatively bounded by worker max., Validate Telegram-only settings at the Stage 8 composition boundary., Return validated private HTTP listener settings when enabled., Runtime settings loaded from environment variables and ``.env``., Settings, check_runtime(), _cleanup() (+19 more)

### Community 28 - "DeepLinkRegistryService"
Cohesion: 0.07
Nodes (41): DeepLinkTargetType, DatabaseConcurrencyError, DeepLinkNotFound, IdempotencyKeyConflict, InvalidTrackUrl, A recognized provider URL targets an out-of-scope non-track entity., A transient write conflict that may be retried as a whole transaction., One bot-scoped idempotency key was reused for a different request. (+33 more)

### Community 29 - "test_stage134_spotify_authorization.py"
Cohesion: 0.10
Nodes (34): _parse_spotify_webapi_submission(), Accept exactly two bounded lines without ever embedding them in an error., FakeConfig, FakeRequests, FakeResponse, FakeSpotifySession, FakeZeroconfServer, Any (+26 more)

### Community 30 - "TelegramUserService"
Cohesion: 0.08
Nodes (47): QueueRuntimeSnapshot, QueueStatusCounts, SingleFlightSnapshot, SubscriberStatusCounts, TelegramCacheStats, WorkerPoolSnapshot, AdminOverview, AdminOverviewError (+39 more)

### Community 31 - "UploadJob"
Cohesion: 0.07
Nodes (17): QueueFullError, Protocol, Authoritative source execution seam for Stage 25-enabled workers., Stage25ExecutionBoundary, WorkerBackend, DownloadJob, UploadJob, _changed() (+9 more)

### Community 32 - "RuntimeError"
Cohesion: 0.10
Nodes (32): ApplicationInstanceAlreadyRunningError, ApplicationInstanceLock, instance_lock_path(), BaseException, Path, Single-host OS advisory lock for one active SQLite application runtime., Non-blocking lifetime lock; file contents are diagnostics, never authority., sqlite_database_path() (+24 more)

### Community 33 - "repositories/__init__.py"
Cohesion: 0.08
Nodes (17): AsyncSession, Repositories, UserDownloadPreferencesRecord, RuntimeSettings, AsyncSession, datetime, Persistence adapter for Stage 22 user preferences., UserDownloadPreferencesRepository (+9 more)

### Community 34 - "SubscriberLifecycleNotifier"
Cohesion: 0.18
Nodes (9): datetime, Event, datetime, Event, Protocol, SubscriberLifecycleNotifier, datetime, Event (+1 more)

### Community 35 - "handlers.py"
Cohesion: 0.15
Nodes (35): AlbumResolutionFailed, A recognized album could not be resolved through its provider boundary., create_stage9_router(), Router, Thin aiogram handlers for the Stage 9 downloader bot., AlbumPageCallback, AlbumQualityCallback, AlbumToggleCallback (+27 more)

### Community 36 - "composition.py"
Cohesion: 0.06
Nodes (32): compose_stage9(), Reusable Stage 8 and Stage 9 application composition roots., Compose the queue/cache/user-delivery runtime without starting polling., The pinned provider boundary cannot reliably resolve this album., UnsupportedAlbum, AlbumSnapshot, MusicProvider, ABC (+24 more)

### Community 37 - "NativeCodec"
Cohesion: 0.11
Nodes (34): DownloadProfileResolver, FormatUnavailable, InvalidDownloadPreferences, ProfileUnavailable, ValueError, A preference combination that cannot be fulfilled semantically., A valid format preference unsupported by the selected source., No mutually supported quality exists for this provider/media item. (+26 more)

### Community 38 - "DownloadArtifactManager"
Cohesion: 0.08
Nodes (37): ArtifactCleanupSummary, Path, Classify with the exact cleanup policy without deleting anything., StaleArtifactCleanupService, ActiveArtifactRegistry, ArtifactPathError, DownloadArtifactManager, Path (+29 more)

### Community 39 - "QualityProfile"
Cohesion: 0.07
Nodes (45): EffectiveDownloadProfile, profile_to_dict(), Stable serialization used by persistence adapters and diagnostics., DownloadAttemptStatus, DownloadFailureCode, StrEnum, QualityProfile, SourceValidationConfidence (+37 more)

### Community 40 - "ProviderRateLimitSnapshot"
Cohesion: 0.40
Nodes (3): ProviderRateLimitSnapshot, Bounded local pacing state; it intentionally says nothing about health., Expose no provider data beyond local operation counts and pacing state.

### Community 41 - "TelegramArtifactCacheService"
Cohesion: 0.08
Nodes (16): MediaArtifactSpec, Immutable, deterministic description of requested output bytes., Any, Stable Stage 24 cache identity for one produced Telegram artifact., TelegramCacheKey, datetime, timedelta, TelegramArtifactCacheService (+8 more)

### Community 42 - "test_stage93_albums.py"
Cohesion: 0.15
Nodes (24): AlbumTooLarge, The provider release exceeds the bounded durable snapshot limit., encode_album_clear_all(), encode_album_download_all(), encode_album_download_selected(), encode_album_first_quality(), encode_album_other_quality(), encode_album_page() (+16 more)

### Community 43 - "test_stage93_album_integration.py"
Cohesion: 0.08
Nodes (33): AlbumItemResolutionStatus, AlbumRequestStatus, AlbumActionOutcome, StrEnum, TelegramAlbumRequest, AlbumAggregate, _changed(), Any (+25 more)

### Community 44 - "test_stage133_deezer_authorization.py"
Cohesion: 0.10
Nodes (24): ProviderSecretInput, Opaque credential input whose repr and str are always redacted., Reveal only at the provider backend boundary., SensitiveValue, DeezerArlAuthorizationBoundary, DeezerArlAuthorizationDriver, DeezerArlAuthorizationResult, _failed() (+16 more)

### Community 45 - "OnTheSpotWorker"
Cohesion: 0.11
Nodes (11): _atomic_onthespot_config_writes(), _atomic_write_private_json(), OnTheSpotWorker, Path, Install bounded adapters for provider logins that handle durable secrets., Compatibility entry point retained for focused Stage 13.3 tests., Release pending state only; persisted accounts are never touched., Inspect selected native media without returning URLs, manifests, or credentials. (+3 more)

### Community 46 - "test_stage9_telegram.py"
Cohesion: 0.18
Nodes (20): StrEnum, TrackRequestActionOutcome, _artifact_metadata(), _drain(), Gateway, IdleProvider, Event, Path (+12 more)

### Community 47 - "OnTheSpotProvider"
Cohesion: 0.06
Nodes (35): AlbumReference, PlaylistReference, Isolated OnTheSpot integration., _album_snapshot(), _bounded_album_text(), _duration_ms(), _normalized_track_metadata(), OnTheSpotProvider (+27 more)

### Community 48 - "test_stage103_runtime_worker_controls.py"
Cohesion: 0.13
Nodes (21): StrEnum, WorkerMutationStatus, create_admin_router(), Router, BlockingPoolBackend, CacheStatsFake, _callback_update(), _control() (+13 more)

### Community 49 - "utc_now"
Cohesion: 0.07
Nodes (12): Best-effort origin-chat guidance for an unreachable USER target., Derive technical identity only from the admitted immutable request., utc_now(), TelegramDeliveryRequest, _changed(), Any, AsyncSession, datetime (+4 more)

### Community 50 - "LocalizationService"
Cohesion: 0.11
Nodes (22): LocalizationError, LocalizationFormatError, A locale catalog or translation template is invalid., A translation could not be formatted with the supplied values., Localization services., _format_fields(), LocalizationService, Any (+14 more)

### Community 51 - "OperationalAuditService"
Cohesion: 0.12
Nodes (22): OperationalAuditActorKind, OperationalAuditEventType, OperationalAuditTargetKind, ArtifactCleanupAuditDetails, AuditEventView, OperationalAuditService, Path, Validated, bounded builders for high-value operational audit history. (+14 more)

### Community 52 - "AdminManagementPresentation"
Cohesion: 0.19
Nodes (20): AdministratorPage, ManagedUser, PromotionCandidatePage, Owner-only management of database-backed administrator roles., AdminManagementCallback, AdminManagementCallbackAction, AdminManagementPresentation, _bounded_int() (+12 more)

### Community 53 - "test_stage28_collection_presentation.py"
Cohesion: 0.24
Nodes (15): BatchStatus, CollectionStatusPresentationService, _text(), _batch(), _collection(), _Gateway, _presentation(), asyncio (+7 more)

### Community 54 - "worker.py"
Cohesion: 0.10
Nodes (20): Private JSON Lines protocol shared by the OnTheSpot parent and worker., _account_bitrate(), _bounded_number(), _is_local_ipv4_address(), main(), _native_media(), Isolated OnTheSpot JSON Lines worker. This module intentionally sets…, Create one bounded Tidal device challenge without exposing its device code. (+12 more)

### Community 55 - "services/singleflight.py"
Cohesion: 0.11
Nodes (20): InvalidRequestKeyError, SubscriberNotFoundError, SubscriberNotReadyError, DeliveryPreparationResult, DownloadJobView, JobSubscriberView, SingleFlightSubmission, Cache-first delivery preparation boundary for future Telegram handlers. (+12 more)

### Community 56 - "TelegramPresentation"
Cohesion: 0.14
Nodes (10): BatchProgress, TrackHistoryEntry, AlbumCard, AlbumSelectionPage, _card(), _bounded(), _format_duration(), InlineKeyboardMarkup (+2 more)

### Community 57 - "PreparedSourceMedia"
Cohesion: 0.18
Nodes (23): PreparedSourceMedia, Normalized facts about provider-native media owned by one Stage 6 job., NativeDownloadBoundary, Enforce a free-space floor before starting a fresh media acquisition., TemporaryDiskGuard, FakeProbe, FakeProvider, FakeTranscoder (+15 more)

### Community 58 - "ProviderAccountStatus"
Cohesion: 0.10
Nodes (27): ProviderAccountOverview, ProviderAccountStatus, ProviderSensitiveInputChallenge, Provider-neutral state safe for presentation, logs, and repr output., Opaque generation for an in-process, non-durable secret-entry flow., Return only the current generation while it is awaiting a first submission., _bounded(), encode_provider_accounts_callback() (+19 more)

### Community 59 - "logging.py"
Cohesion: 0.07
Nodes (30): Container-safe application logging and centralized secret redaction., Return log-safe text while preserving ordinary public/job identifiers., Backward-compatible record filter using the centralized redactor., Format one-line text logs, including safe async-flow identifiers., redact_secrets(), RedactingFormatter, SecretRedactionFilter, _directory_size() (+22 more)

### Community 60 - "ProviderAuthorizationMethod"
Cohesion: 0.09
Nodes (27): ProviderAuthorizationMethod, ProviderAuthorizationOutcomeStatus, ProviderAuthorizationRequest, Exception, Protocol, StrEnum, Sanitized Tidal device-flow driver over the isolated provider child boundary., Poll one bounded child operation at a time and verify runtime truth on success. (+19 more)

### Community 61 - "_worker_with_tidal"
Cohesion: 0.17
Nodes (18): _device_response(), FakeConfig, FakeRequests, FakeResponse, Any, Exception, MonkeyPatch, parametrize (+10 more)

### Community 62 - "_worker_with_deezer"
Cohesion: 0.14
Nodes (18): _anonymous_payload(), FakeConfig, FakeRequests, FakeResponse, FakeSession, Any, Exception, MonkeyPatch (+10 more)

### Community 63 - "database.py"
Cohesion: 0.10
Nodes (20): Any, Connection, Async database lifecycle and isolated SQLite configuration., Read an allow-listed SQLite PRAGMA for diagnostics and tests., _read_pragma(), scalar_pragma(), Persistence implementation., main() (+12 more)

### Community 64 - "BatchDownloadRepository"
Cohesion: 0.12
Nodes (9): BatchItemStatus, BatchDownloadRequest, BatchDownloadRepository, AsyncSession, datetime, Atomic persistence operations for Stage 23 batches., Persist metadata and every member in one transaction., Atomically reserve one item for admission. The reservation uses the existing… (+1 more)

### Community 65 - "User"
Cohesion: 0.13
Nodes (4): Compatibility alias for the pre-Stage-9 internal field name., User, AsyncSession, UserRepository

### Community 66 - "ResolveTrackService"
Cohesion: 0.06
Nodes (78): ProviderDiscoveryStatus, TrackEvidenceCode, TrackMatchDecision, DatabaseError, A provider identity is already owned by a different canonical Track., TrackSourceOwnershipConflict, NormalizedTrackMetadata, ProviderDiscoveryResult (+70 more)

### Community 67 - "ops.py"
Cohesion: 0.08
Nodes (35): require_current_schema(), _schema_revision(), Conservative cleanup of stale, unowned Stage 6 artifact roots., is_artifact_job_id(), Controlled temporary artifact ownership for one-shot Stage 6 jobs., CrashRecoveryService, CrashRecoverySummary, datetime (+27 more)

### Community 68 - "ProviderAccountManagementService"
Cohesion: 0.21
Nodes (4): _error_status(), ProviderAccountManagementService, Reconcile child-owned durable/runtime truth without making provider failure…, test_runtime_composition_starts_and_stops_without_leaked_tasks()

### Community 69 - "test_onthespot_provider.py"
Cohesion: 0.17
Nodes (16): FakeProcessClient, _provider(), Any, asyncio, parametrize, test_detects_and_canonicalizes_supported_track_url(), test_passes_only_canonical_url_and_maps_safe_metadata(), test_playlist_snapshot_is_normalized_with_immutable_order() (+8 more)

### Community 70 - "repositories/singleflight.py"
Cohesion: 0.15
Nodes (8): legacy_quality_fingerprint(), Any, Stable identity for pre-Stage-26 queue callers., CancellationRecord, AsyncSession, datetime, Persistent SingleFlight admission and subscriber lifecycle operations., SingleFlightRepository

### Community 71 - "test_stage9_callbacks.py"
Cohesion: 0.15
Nodes (21): Track, _track_card(), TrackCard, encode_first_quality(), encode_locale(), encode_other_quality(), encode_setting_quality(), encode_track_back() (+13 more)

### Community 72 - "What You Must Do When Invoked"
Cohesion: 0.08
Nodes (24): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+16 more)

### Community 73 - "test_stage134_spotify_telegram.py"
Cohesion: 0.20
Nodes (18): _bot_side_effect(), CacheStatsFake, CaptureDriver, _create_user(), _message_update(), PlaybackDriver, Any, Bot (+10 more)

### Community 74 - "ProviderResolutionRepository"
Cohesion: 0.18
Nodes (8): DownloadProviderAttemptRecord, DownloadProviderCandidateRecord, ProviderResolutionRepository, AsyncSession, datetime, Atomic persistence helpers for Stage 25 snapshots and audit attempts., Return the provider that actually produced the successful artifact., Reconcile attempts left open by a dead lifecycle worker lease.

### Community 75 - "admin_handlers.py"
Cohesion: 0.16
Nodes (17): AdminManagementErrorCode, StrEnum, _close_panel(), _deny_answered_callback(), _deny_callback(), _edit_or_send(), _management_error_key(), _observe_callback() (+9 more)

### Community 76 - "exceptions.py"
Cohesion: 0.07
Nodes (46): do_run_migrations(), include_object(), Async Alembic migration environment., run_async_migrations(), run_migrations_offline(), _empty_to_none(), get_settings(), _normalize_locale() (+38 more)

### Community 77 - "TelegramAlbumRequestService"
Cohesion: 0.25
Nodes (3): AlbumActionResult, Event, TelegramAlbumRequestService

### Community 78 - "models.py"
Cohesion: 0.14
Nodes (33): DownloadPlanOperation, DownloadPlanReadiness, DownloadPlanReason, QualityCandidateRejectionReason, DownloadPlan, DownloadProviderCandidate, QualityProviderDiagnostic, QualityResolutionResult (+25 more)

### Community 79 - "DownloadLifecycleService"
Cohesion: 0.05
Nodes (45): DownloadLifecycle, InvalidDownloadTransition, ValueError, Pure Stage 21 state-machine validation., DownloadJobStatus, DownloadPhase, Durable user-facing download lifecycle (distinct from technical queue jobs)., datetime (+37 more)

### Community 80 - "test_onthespot_worker.py"
Cohesion: 0.17
Nodes (18): FakeAccounts, FakeRegistry, Any, MonkeyPatch, parametrize, Path, test_authenticated_provider_without_active_account_requires_auth(), test_available_source_returns_only_normalized_facts() (+10 more)

### Community 81 - "TrackReference"
Cohesion: 0.17
Nodes (23): DeepLinkStatus, UnsupportedProvider, Resolve one track from stable provider identity., TrackReference, AsyncClient, _auth(), _client(), LogCaptureFixture (+15 more)

### Community 82 - ".spotify_webapi_authorize"
Cohesion: 0.22
Nodes (5): _normalize_spotify_webapi_credential(), Validate a Developer pair before replacing the two OnTheSpot config keys., _spotify_response_reason(), _spotify_webapi_failure(), _SpotifyWebApiValidation

### Community 83 - "integration/test_stage132_tidal_authorization.py"
Cohesion: 0.22
Nodes (7): ProviderAuthorizationStartStatus, AccountBackend, BlockingDriver, test_only_authoritative_owner_can_start_real_tidal_method(), test_ready_tidal_account_does_not_start_replacement_flow(), test_spotify_and_deezer_authorization_remain_unsupported(), _user()

### Community 84 - "Stage 26 — Metadata & Media Processing"
Cohesion: 0.09
Nodes (21): 10. Errors, retries, and ownership, 11. Collection behavior, 12. Acceptance matrix, 13. Schema decision, 14. Minimal implementation plan, 15. Required Stage 26 acceptance tests, 16. Regression boundaries, 17. Required production-level proofs (+13 more)

### Community 85 - "test_config.py"
Cohesion: 0.21
Nodes (20): make_settings(), MonkeyPatch, parametrize, test_application_log_level_is_normalized(), test_default_locale_must_be_supported(), test_empty_owner_id_remains_optional(), test_enabled_internal_api_requires_a_strong_trimmed_token(), test_enabled_internal_api_returns_validated_listener_configuration() (+12 more)

### Community 86 - "Any"
Cohesion: 0.13
Nodes (17): _album_positive_int(), _album_text(), _download_media(), _health_result(), Any, Exception, Run only pinned service download/decryption code; skip all quality post-…, Return only normalized readiness facts; never return account or token data. (+9 more)

### Community 87 - "Stage9Components"
Cohesion: 0.10
Nodes (9): Stage9Components, Owned periodic task; unexpected termination is observable by supervision., StaleArtifactCleanupManager, TelegramAlbumCoordinatorManager, TelegramDeliveryFanoutManager, Best-effort Telegram rendering; lifecycle correctness never depends on it., Best-effort bounded repair after durable recovery has completed., TelegramStatusPresentationService (+1 more)

### Community 88 - "Musicbot Downloader"
Cohesion: 0.10
Nodes (20): Architecture, Current limitations, Database migrations, Development checks, Download tool, Internal API and Telegram deep links (Stage 11), Internationalization, Live Provider Health (+12 more)

### Community 89 - "test_stage133_deezer_telegram.py"
Cohesion: 0.23
Nodes (16): _bot_side_effect(), CacheStatsFake, CaptureDriver, _create_user(), _message_update(), Any, Bot, ChatType (+8 more)

### Community 90 - "_pinned_worker"
Cohesion: 0.22
Nodes (15): _pinned_worker(), PinnedConfig, Any, LogCaptureFixture, MonkeyPatch, parametrize, Path, test_atomic_update_replaces_whole_config_with_restrictive_permissions() (+7 more)

### Community 91 - "UserRole"
Cohesion: 0.17
Nodes (21): UserRole, AdministratorManagementService, AdminManagementError, AdminMutationResult, AdminMutationStatus, _managed(), _page_bounds(), Authorize every read/change and expose only Stage 10.2 transitions. (+13 more)

### Community 92 - "test_stage123_ops.py"
Cohesion: 0.22
Nodes (16): Path, Validated SQLite online backup with an atomic, non-overwriting destination., SQLiteBackupResult, SQLiteBackupService, _make_recovery_service(), _offline_artifacts(), _offline_recovery(), _current_migration_head() (+8 more)

### Community 93 - "test_stage11_telegram_deep_links.py"
Cohesion: 0.11
Nodes (22): AlbumTrackSnapshot, Event, Protocol, QualitySelectionResult, TelegramTrackRequestService, TelegramTrackResolver, TrackRequestActionResult, TelegramHandlerDependencies (+14 more)

### Community 94 - "TelegramFileCache"
Cohesion: 0.21
Nodes (6): TelegramFileCache, AsyncSession, datetime, SQLite operations for the bot-scoped Telegram completed-result cache., Reserve the SQLite writer lock before the cache/flight admission decision., TelegramFileCacheRepository

### Community 95 - "_AtomicReplaceTextFile"
Cohesion: 0.14
Nodes (6): _AtomicReplaceTextFile, _deezer_failure(), _DeezerValidatedSession, _normalize_deezer_arl(), _parse_deezer_user_data(), Validate through HTTPS before writing an OnTheSpot-owned account record.

### Community 96 - ".quality_profile"
Cohesion: 0.12
Nodes (7): upgrade(), upgrade(), upgrade(), downgrade(), upgrade(), upgrade(), Map the neutral quality tier to the existing Stage 21 pipeline contract.

### Community 97 - "queues.py"
Cohesion: 0.12
Nodes (16): UploadJobView, WorkerSettingsSnapshot, WorkerSettingValues, Typed Stage 7 queue, upload, and runtime-setting service boundaries., _upload_view(), _validate_page(), WorkerPoolResizer, WorkerSettingMutation (+8 more)

### Community 98 - "README.md"
Cohesion: 0.20
Nodes (4): Architecture and authority, Explicit non-goals, Stage 13.1 — Provider Account Management Foundation, Stage 13.2 — Tidal Device Authorization

### Community 99 - "CallbackQuery"
Cohesion: 0.35
Nodes (12): _album_bulk_selection(), _answer_action_failure(), _answer_album_failure(), _edit_or_send(), _invalid_callback(), _observe_callback(), CallbackQuery, InlineKeyboardMarkup (+4 more)

### Community 100 - "._close_spotify_playback_pairing"
Cohesion: 0.20
Nodes (5): _close_pinned_zeroconf_server(), Idempotently close only the matching generation., Compensate for librespot 0.0.10 HttpRunner.close() being a no-op., Atomically remove one managed provider's OnTheSpot-owned authentication state., _remove_spotify_pairing_file()

### Community 101 - "AsyncWorkerPool"
Cohesion: 0.23
Nodes (3): AsyncWorkerPool, Task, _WorkerSlot

### Community 102 - "InternalApiServer"
Cohesion: 0.21
Nodes (6): _EmbeddedUvicornServer, InternalApiServer, FastAPI, Embedded non-blocking uvicorn lifecycle., Leave process signals under the application-level supervisor., Wait for unexpected listener termination so the process supervisor can fail.

### Community 103 - ".tidal_device_authorization_poll"
Cohesion: 0.50
Nodes (3): Perform at most one token-endpoint request for an existing device flow., _tidal_account_from_token_payload(), _tidal_poll_result()

### Community 104 - "Message"
Cohesion: 0.29
Nodes (8): _observe_message(), _private(), _profile(), AiogramUser, Message, _record_card_message(), _send_album_request_card(), _send_track_request_card()

### Community 105 - "FakeAiogramBot"
Cohesion: 0.32
Nodes (3): FakeAiogramBot, FakeSession, Any

### Community 106 - "test_stage24_history_cache.py"
Cohesion: 0.32
Nodes (8): encode_history_batch(), encode_history_batch_repeat(), encode_history_list(), encode_history_repeat(), encode_history_track(), HistoryCallback, parse_history_callback(), test_stage24_history_callbacks_are_compact_and_strict()

### Community 107 - "Production deployment and release validation (Stage 12.4)"
Cohesion: 0.17
Nodes (12): Backup, upgrade, and rollback, Configuration, Crash and atomicity semantics, Filesystem and permissions, Health and readiness, Initialization and operation, Licensing / distribution review, Logs and disk protection (+4 more)

### Community 108 - "Downloader release checklist"
Cohesion: 0.17
Nodes (12): 2026-08-20 local deterministic validation, 2026-08-21 local deterministic validation, 2026-08-24 Stage 13.4 local deterministic validation, Application and security regressions, Candidate and host, Downloader release checklist, Filesystem, lifecycle, recovery, and backup, Licensing / distribution review (+4 more)

### Community 109 - "Stage 11 Internal API and Deep-Link Registry"
Cohesion: 0.17
Nodes (11): Authentication, Boundary and lifecycle, Configuration, Endpoints, Inspect, Register, Registry and token design, Revoke (+3 more)

### Community 110 - "test_migrations.py"
Cohesion: 0.30
Nodes (11): Path, test_provider_enum_migration_converts_existing_rows(), test_stage11_migration_enforces_registry_target_and_bot_scope(), test_stage123_audit_migration_upgrades_0010_and_round_trips(), test_stage19_migration_is_independent_and_round_trips_with_legacy_backfill(), test_stage71_migration_upgrades_stage7_and_round_trips(), test_stage7_migration_creates_clean_queue_schema(), test_stage8_migration_upgrades_0005_and_round_trips() (+3 more)

### Community 111 - "Stage 15 — Track Search Architecture"
Cohesion: 0.18
Nodes (9): Architecture, Compatibility, Domain and contracts, Stage 15 — Track Search Architecture, UX state and scope, Adapter architecture, Availability and compatibility, Mapping and normalization (+1 more)

### Community 112 - "OnTheSpot v1.8.1 provider capability matrix"
Cohesion: 0.33
Nodes (5): OnTheSpot v1.8.1 provider capability matrix, Runtime interpretation, Stage 10.4 provider-level health audit, Stage 6 native-download audit, Stage 9.3 album snapshot audit

### Community 113 - ".create_from_collection"
Cohesion: 0.50
Nodes (4): ActiveBatchLimitExceeded, BatchLimitExceeded, ValueError, Persist an already-resolved immutable collection snapshot.

### Community 114 - "Stage 29 — Recognition 2.0"
Cohesion: 0.40
Nodes (5): Acceptance matrix, Confirmation and security, Contract, Non-goals, Stage 29 — Recognition 2.0

### Community 115 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 116 - "Stage 27 — Production Limits, Resource Safety & Observability"
Cohesion: 0.22
Nodes (8): Acceptance and non-goals, Authority map, Configuration and policies, Diagnostics, Goal and boundary, Recovery and lease invariant, Stage 27 — Production Limits, Resource Safety & Observability, Storage pressure

### Community 118 - "DownloadWorkspaceManager"
Cohesion: 0.29
Nodes (4): DownloadWorkspaceManager, Path, Conservative per-job workspace ownership and orphan cleanup., test_stage21_workspace_isolated_and_orphans_removed()

### Community 119 - "Stage 20 — Architecture & Production Review"
Cohesion: 0.25
Nodes (7): Architecture decisions left unchanged, Baseline, Confirmed defects and fixes, Remaining known risks, Stage 20 — Architecture & Production Review, Tests and migration result, Validation evidence

### Community 120 - "Stage 7 queue lifecycle"
Cohesion: 0.25
Nodes (7): Crash reconciliation and waiting, Ownership, Runtime pools, Stage 7.1 SingleFlight admission, Stage 7 queue lifecycle, State transitions, Subscriber and flight lifecycle

### Community 121 - "Stage 8 Telegram cache and delivery foundation"
Cohesion: 0.25
Nodes (7): Cache and upload lifecycle, Deliberate limitations, Inspection and manual verification, Operator setup, Opt-in external verification, Runtime boundaries, Stage 8 Telegram cache and delivery foundation

### Community 125 - "Stage 13.3 — Deezer ARL Authorization"
Cohesion: 0.29
Nodes (6): Isolated validation and persistence, Lifecycle and authority, Scope, Secure runtime reload and status sanitization, Stage 13.3 — Deezer ARL Authorization, Telegram deletion gate and transient parent handling

### Community 126 - "Stage 13.4 — Spotify Playback and Search Credentials"
Cohesion: 0.29
Nodes (6): Component readiness, Local discovery and containers, Playback pairing, Scope, Search / Web API credentials, Stage 13.4 — Spotify Playback and Search Credentials

### Community 127 - "Stage 13.5 — Provider Lifecycle, Security, and Crash Hardening"
Cohesion: 0.29
Nodes (6): Atomic updates and reset, Backup, restore, and external limits, Credential ownership and lifecycle, Secret and filesystem guarantees, Stage 13.5 — Provider Lifecycle, Security, and Crash Hardening, Startup reconciliation and child recovery

### Community 128 - "Stage 14 — Telegram Bot UX Foundation"
Cohesion: 0.29
Nodes (6): Architecture, Callbacks and state, Error boundary and compatibility, Messages and menus, New modules, Stage 14 — Telegram Bot UX Foundation

### Community 129 - "Stage 17 — Track Recognition"
Cohesion: 0.33
Nodes (6): Architecture, Deterministic scoring and ranking, Domain models, Replacement and compatibility seams, Stage 17 — Track Recognition, Tests

### Community 130 - "Stage 18 — Download Flow UX"
Cohesion: 0.33
Nodes (6): Delivery separation, Download request and confirmation, Existing architecture reused, Stage 18 — Download Flow UX, Telegram UX and states, Validation

### Community 131 - "Stage 19 — Channel/Bot Integration"
Cohesion: 0.29
Nodes (6): Callback and permission security, Chat policy and channel binding, Context and delivery model, Stage 19 — Channel/Bot Integration, Validation, Workflow and boundaries

### Community 132 - "20260817_0003_track_identity_keys.py"
Cohesion: 0.53
Nodes (4): _identity_keys(), _normalize(), _normalize_isrc(), upgrade()

### Community 134 - "graphify reference: query, path, explain"
Cohesion: 0.33
Nodes (5): For /graphify explain, For /graphify path, graphify reference: query, path, explain, Step 0 — Constrained query expansion (REQUIRED before traversal), Step 1 — Traversal

### Community 135 - "test_onthespot_external.py"
Cohesion: 0.60
Nodes (5): asyncio, external, test_public_source_readiness_when_explicitly_enabled(), test_real_onthespot_metadata_when_explicitly_enabled(), test_real_onthespot_search_when_explicitly_enabled()

### Community 137 - "Stage 28 — Telegram UX & Interaction Consistency"
Cohesion: 0.40
Nodes (4): Current implementation boundary, Deliberately incomplete work, Safety invariants, Stage 28 — Telegram UX & Interaction Consistency

### Community 138 - "validate-production.sh"
Cohesion: 0.60
Nodes (3): run_data(), run_fixture(), validate-production.sh script

### Community 140 - "graphify reference: add a URL and watch a folder"
Cohesion: 0.50
Nodes (3): For /graphify add, For --watch, graphify reference: add a URL and watch a folder

### Community 141 - "graphify reference: commit hook and native CLAUDE.md integration"
Cohesion: 0.50
Nodes (3): For git commit hook, For native CLAUDE.md integration, graphify reference: commit hook and native CLAUDE.md integration

### Community 142 - "graphify reference: incremental update and cluster-only"
Cohesion: 0.50
Nodes (3): For --cluster-only, For --update (incremental re-extraction), graphify reference: incremental update and cluster-only

## Knowledge Gaps
- **195 isolated node(s):** `musicbot-downloader`, `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)`, `Step 1 - Ensure graphify is installed` (+190 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 1186 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **16 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `MusicProviderName` connect `MusicProviderName` to `unit/test_stage18_download.py`, `Track`, `workers.py`, `test_stage25_worker_execution.py`, `track_recognition.py`, `ProviderAccountErrorCode`, `integration/test_stage18_download.py`, `services/download_lifecycle.py`, `enums.py`, `ProviderAuthorizationOutcome`, `test_onthespot_external.py`, `TelegramContext`, `ProviderUnavailable`, `integration/test_stage15_search.py`, `SingleFlightService`, `media.py`, `unit/test_stage104_provider_health.py`, `ProviderRuntimeStatus`, `TelegramAuthorizationService`, `models/base.py`, `Database`, `test_stage27_production_hardening.py`, `DeepLinkRegistryService`, `test_stage134_spotify_authorization.py`, `TelegramUserService`, `UploadJob`, `repositories/__init__.py`, `composition.py`, `NativeCodec`, `DownloadArtifactManager`, `QualityProfile`, `ProviderRateLimitSnapshot`, `test_stage93_albums.py`, `test_stage93_album_integration.py`, `test_stage133_deezer_authorization.py`, `OnTheSpotWorker`, `test_stage9_telegram.py`, `OnTheSpotProvider`, `test_stage103_runtime_worker_controls.py`, `OperationalAuditService`, `test_stage28_collection_presentation.py`, `worker.py`, `PreparedSourceMedia`, `ProviderAccountStatus`, `ProviderAuthorizationMethod`, `BatchDownloadRepository`, `ResolveTrackService`, `ProviderAccountManagementService`, `test_onthespot_provider.py`, `test_stage134_spotify_telegram.py`, `ProviderResolutionRepository`, `admin_handlers.py`, `TelegramAlbumRequestService`, `models.py`, `DownloadLifecycleService`, `TrackReference`, `integration/test_stage132_tidal_authorization.py`, `Any`, `Stage9Components`, `test_stage133_deezer_telegram.py`, `test_stage11_telegram_deep_links.py`, `TelegramFileCache`?**
  _High betweenness centrality (0.195) - this node is a cross-community bridge._
- **Why does `QualityProfile` connect `QualityProfile` to `unit/test_stage18_download.py`, `workers.py`, `test_stage25_worker_execution.py`, `integration/test_stage18_download.py`, `enums.py`, `TelegramContext`, `SingleFlightService`, `media.py`, `ProviderRuntimeStatus`, `QueueErrorCode`, `models/base.py`, `Database`, `TelegramUserService`, `UploadJob`, `repositories/__init__.py`, `handlers.py`, `composition.py`, `NativeCodec`, `DownloadArtifactManager`, `test_stage93_albums.py`, `test_stage93_album_integration.py`, `test_stage9_telegram.py`, `test_stage103_runtime_worker_controls.py`, `utc_now`, `services/singleflight.py`, `TelegramPresentation`, `PreparedSourceMedia`, `database.py`, `User`, `repositories/singleflight.py`, `test_stage9_callbacks.py`, `exceptions.py`, `TelegramAlbumRequestService`, `models.py`, `UserRole`, `test_stage11_telegram_deep_links.py`, `TelegramFileCache`, `.quality_profile`, `queues.py`?**
  _High betweenness centrality (0.054) - this node is a cross-community bridge._
- **Why does `compose_stage9()` connect `composition.py` to `unit/test_stage18_download.py`, `Track`, `workers.py`, `TelegramUploadReceipt`, `test_stage25_worker_execution.py`, `track_recognition.py`, `ProviderAccountErrorCode`, `integration/test_stage18_download.py`, `enums.py`, `ProviderAuthorizationOutcome`, `TelegramContext`, `MusicProviderName`, `integration/test_stage15_search.py`, `SingleFlightService`, `media.py`, `unit/test_stage104_provider_health.py`, `TelegramDeliveryStatus`, `ProviderRuntimeStatus`, `RuntimeWorkerControlService`, `TelegramAuthorizationService`, `Database`, `ux_handlers.py`, `test_stage27_production_hardening.py`, `Settings`, `DeepLinkRegistryService`, `TelegramUserService`, `handlers.py`, `DownloadArtifactManager`, `QualityProfile`, `TelegramArtifactCacheService`, `test_stage133_deezer_authorization.py`, `test_stage9_telegram.py`, `test_stage103_runtime_worker_controls.py`, `LocalizationService`, `AdminManagementPresentation`, `test_stage28_collection_presentation.py`, `TelegramPresentation`, `PreparedSourceMedia`, `ProviderAccountStatus`, `ProviderAuthorizationMethod`, `ResolveTrackService`, `ops.py`, `ProviderAccountManagementService`, `TelegramAlbumRequestService`, `DownloadLifecycleService`, `Stage9Components`, `UserRole`, `test_stage11_telegram_deep_links.py`, `queues.py`?**
  _High betweenness centrality (0.038) - this node is a cross-community bridge._
- **Are the 310 inferred relationships involving `MusicProviderName` (e.g. with `compose_stage9()` and `AlbumSnapshot`) actually correct?**
  _`MusicProviderName` has 310 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `Database` (e.g. with `OwnerBootstrapService` and `ResolveTrackService`) actually correct?**
  _`Database` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 193 inferred relationships involving `QualityProfile` (e.g. with `DownloadOptions` and `EffectiveDownloadProfile`) actually correct?**
  _`QualityProfile` has 193 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `LocalizationService` (e.g. with `LocalizationError` and `LocalizationFormatError`) actually correct?**
  _`LocalizationService` has 2 INFERRED edges - model-reasoned connections that need verification._