# Graph Report - musicbot-downloader  (2026-09-05)

## Corpus Check
- 367 files · ~215,419 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 4798 nodes · 18112 edges · 202 communities (139 shown, 19 thin omitted)
- Extraction: 84% EXTRACTED · 16% INFERRED · 0% AMBIGUOUS · INFERRED: 2896 edges (avg confidence: 0.95)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `30d8a6ec`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Track
- compose_stage9
- DownloadArtifactManager
- TelegramUploadReceipt
- stage25_execution.py
- unit/test_stage17_track_recognition.py
- OnTheSpotProvider
- test_stage8_telegram_cache.py
- DownloadLifecycleService
- UserDownloadPreferences
- ProviderAuthorizationOutcome
- TelegramContext
- MusicProviderName
- ProviderUnavailable
- ux_handlers.py
- SingleFlightService
- NativeCodec
- ProviderHealthService
- UxFlowService
- ProviderRuntimeStatus
- admin_handlers.py
- exceptions.py
- UserRole
- models/base.py
- Database
- DownloadFailureCode
- BatchDownloadService
- Settings
- DeepLinkRegistryService
- test_stage134_spotify_authorization.py
- test_stage10_admin.py
- DownloadJobRepository
- RuntimeError
- repositories/__init__.py
- workers.py
- handlers.py
- MusicProvider
- composition.py
- test_stage122_crash_recovery.py
- QualityProfile
- test_stage27_production_hardening.py
- TelegramArtifactCacheService
- presentation.py
- TelegramAlbumRepository
- test_stage133_deezer_authorization.py
- OnTheSpotWorker
- test_stage9_telegram.py
- TrackReference
- test_stage103_runtime_worker_controls.py
- TelegramDeliveryStatus
- LocalizationService
- OperationalAuditService
- AdminManagementPresentation
- test_stage28_collection_presentation.py
- worker.py
- ResolveTrackService
- TelegramPresentation
- PreparedSourceMedia
- ProviderAccountsPresentation
- test_stage121_runtime_hardening.py
- TidalDeviceAuthorizationDriver
- unit/test_stage132_tidal_authorization.py
- _worker_with_deezer
- enums.py
- BatchDownloadRepository
- User
- track_identity.py
- ops.py
- test_stage93_album_integration.py
- UnsupportedProvider
- repositories/singleflight.py
- test_stage9_callbacks.py
- What You Must Do When Invoked
- test_stage134_spotify_telegram.py
- test_resolution_service.py
- ProviderAvailability
- config.py
- TelegramAlbumRequestService
- core/quality.py
- download_activity.py
- test_onthespot_worker.py
- test_stage11_internal_api.py
- Any
- TelegramDeliveryRequest
- Stage 26 — Metadata & Media Processing
- test_config.py
- WorkerError
- utc_now
- Musicbot Downloader
- test_stage133_deezer_telegram.py
- _pinned_worker
- AdministratorManagementService
- test_stage123_ops.py
- test_stage11_telegram_deep_links.py
- test_track_identity.py
- _AtomicReplaceTextFile
- .quality_profile
- WorkerSettingsService
- README.md
- track_resolution.py
- crash_recovery.py
- AsyncWorkerPool
- InternalApiServer
- .tidal_device_authorization_start
- QueueManager
- DeepLinkRegistryEntry
- test_stage24_history_cache.py
- Production deployment and release validation (Stage 12.4)
- Downloader release checklist
- Stage 11 Internal API and Deep-Link Registry
- test_migrations.py
- Stage 15 — Track Search Architecture
- track_matching.py
- download_history.py
- UserDownloadPreferencesRecord
- graphify reference: extra exports and benchmark
- Stage 27 — Production Limits, Resource Safety & Observability
- ProviderAccountRuntimeProbe
- DownloadWorkspaceManager
- Stage 20 — Architecture & Production Review
- Stage 7 queue lifecycle
- Stage 8 Telegram cache and delivery foundation
- TelegramDeliveryFanoutManager
- TelegramStatusPresentationService
- resolve.py
- Stage 13.3 — Deezer ARL Authorization
- Stage 13.4 — Spotify Playback and Search Credentials
- Stage 13.5 — Provider Lifecycle, Security, and Crash Hardening
- Stage 14 — Telegram Bot UX Foundation
- Stage 17 — Track Recognition
- Stage 18 — Download Flow UX
- Stage 19 — Channel/Bot Integration
- 20260817_0003_track_identity_keys.py
- _require_text
- graphify reference: query, path, explain
- test_onthespot_external.py
- .__init__
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
1. `MusicProviderName` - 540 edges
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
- `test_stage15_use_case_and_error_boundary_report_unavailable_search()` --uses--> `UxErrorMessage`  [INFERRED]
  tests/unit/test_stage15_search.py → app/application/ux/services/errors.py
- `test_stage18_confirmation_callbacks_and_states_remain_opaque_and_validated()` --uses--> `UxState`  [INFERRED]
  tests/unit/test_stage18_download.py → app/application/ux/services/state.py

## Import Cycles
- None detected.

## Communities (202 total, 19 thin omitted)

### Community 0 - "Track"
Cohesion: 0.05
Nodes (66): DownloadConfirmation, DownloadService, DownloadSubmissionPort, DownloadTrackUseCase, datetime, Protocol, timedelta, Stage 18 confirmation and download-admission application flow. (+58 more)

### Community 1 - "compose_stage9"
Cohesion: 0.06
Nodes (75): Stage 15 search and Stage 17 recognition application flow., Search normalized catalogs and optionally recognize the leading intended track., Run Stage 17 after the unchanged Stage 15 search-result boundary., SearchTracksUseCase, UxErrorService, compose_stage9(), Compose the queue/cache/user-delivery runtime without starting polling., ProviderAuthenticationError (+67 more)

### Community 2 - "DownloadArtifactManager"
Cohesion: 0.06
Nodes (57): QueueJobStatus, DownloadJobView, UploadJobView, ArtifactPathError, DownloadArtifactManager, Path, ValueError, Controlled temporary artifact ownership for one-shot Stage 6 jobs. (+49 more)

### Community 3 - "TelegramUploadReceipt"
Cohesion: 0.05
Nodes (40): TelegramMediaKind, TelegramBotIdentity, TelegramUploadReceipt, Backward-compatible record filter using the centralized redactor., SecretRedactionFilter, telegram_media_kind(), AiogramTelegramGateway, _normalized_error() (+32 more)

### Community 4 - "stage25_execution.py"
Cohesion: 0.05
Nodes (59): ProviderAccountHealthState, candidate_supports_profile(), canonical_identity_from_track(), canonical_identity_from_values(), CanonicalMediaIdentity, match_candidates(), match_media(), MatchMethod (+51 more)

### Community 5 - "unit/test_stage17_track_recognition.py"
Cohesion: 0.05
Nodes (70): _normalize_optional_text(), StrEnum, RankedTrackCandidate, Provider-independent domain models for Stage 17 track recognition., The top ranked recognition candidate and any non-selected alternatives., Convenience access to the leading normalized track for future presentation…, The next interaction required for a ranked recognition candidate., A normalized catalog track plus opaque source and optional search metadata. (+62 more)

### Community 6 - "OnTheSpotProvider"
Cohesion: 0.05
Nodes (56): ProviderAccountComponent, ProviderAccountComponentStatus, ProviderAccountErrorCode, ProviderAccountState, ProviderAuthorizationOutcomeStatus, ProviderDisconnectOutcomeStatus, ProviderOperationalState, StrEnum (+48 more)

### Community 7 - "test_stage8_telegram_cache.py"
Cohesion: 0.06
Nodes (59): compose_stage8(), Event, Stage8Components, DeliveryPreparationStatus, TelegramCacheStatus, DeliveryInvariantError, A READY subscriber has no matching active completed-result cache entry., SubscriberNotReadyError (+51 more)

### Community 8 - "DownloadLifecycleService"
Cohesion: 0.05
Nodes (42): DeliveryTargetType, StrEnum, DownloadLifecycle, InvalidDownloadTransition, ValueError, Pure Stage 21 state-machine validation., DownloadDeliveryStatus, DownloadJobStatus (+34 more)

### Community 9 - "UserDownloadPreferences"
Cohesion: 0.07
Nodes (57): default_preferences(), DownloadProfileResolver, EffectiveDownloadProfile, FormatUnavailable, InvalidDownloadPreferences, profile_to_dict(), ProfileUnavailable, ValueError (+49 more)

### Community 10 - "ProviderAuthorizationOutcome"
Cohesion: 0.07
Nodes (45): ProviderAuthorizationChallenge, ProviderAuthorizationMethod, ProviderAuthorizationOutcome, ProviderAuthorizationRequest, ProviderAuthorizationStartOutcome, ProviderAuthorizationStartStatus, ProviderCompoundCredentialInput, ProviderLocalPairingChallenge (+37 more)

### Community 11 - "TelegramContext"
Cohesion: 0.07
Nodes (51): ChannelTarget, delivery_target_from_values(), DeliveryTarget, GroupChatTarget, A completed artifact destination, independent from the requesting user., ChannelBinding, ChannelBindingStatus, ChatPolicy (+43 more)

### Community 12 - "MusicProviderName"
Cohesion: 0.05
Nodes (28): MusicProviderName, ProviderAccountOverview, ProviderAccountStatus, ProviderDisconnectOutcome, Provider-neutral state safe for presentation, logs, and repr output., ProviderAccountBackend, _provider_album_url(), _provider_track_url() (+20 more)

### Community 13 - "ProviderUnavailable"
Cohesion: 0.07
Nodes (38): MetadataUnavailable, ProviderOperationTimeout, ProviderUnavailable, A bounded provider operation exceeded its execution timeout., close_shared_process_client(), OnTheSpotProcessClient, Any, Path (+30 more)

### Community 14 - "ux_handlers.py"
Cohesion: 0.06
Nodes (61): StrEnum, UxMenu, encode_ux_callback(), parse_ux_callback(), Versioned, validated callback codec for Stage 14 UX navigation., UxCallback, _validate_part(), DownloadCallback (+53 more)

### Community 15 - "SingleFlightService"
Cohesion: 0.07
Nodes (44): SubscriberStatus, JobSubscriberView, SingleFlightSubmission, _submission_view(), _subscriber_view(), datetime, Event, Protocol (+36 more)

### Community 16 - "NativeCodec"
Cohesion: 0.08
Nodes (53): NativeCodec, NativeContainer, OutputSpecification, Facts Stage 6 must verify before executing one quality strategy., Exact delivery contract represented by a user QualityProfile., SourceMediaRequirement, _media(), provider_sort_key() (+45 more)

### Community 17 - "ProviderHealthService"
Cohesion: 0.06
Nodes (38): ProviderHealthErrorCode, ProviderHealthStatus, Provider-level readiness, deliberately separate from TrackSource status., ProviderHealthEntry, ProviderHealthSnapshot, Sanitized provider-level readiness; never contains account identity or secrets., Read a sanitized provider-level observation from the isolated worker., ProviderHealthProbe (+30 more)

### Community 18 - "UxFlowService"
Cohesion: 0.07
Nodes (37): User interaction flows., _context_for(), Stage 14 navigation flow with no Telegram presentation types., Apply a transport-neutral state transition after a confirmed UX action., Coordinates observation and navigation; handlers only render its result., UxFlowService, UxScreen, Stage 14 user-experience application contracts. (+29 more)

### Community 19 - "ProviderRuntimeStatus"
Cohesion: 0.11
Nodes (46): ProviderResolutionStatus, ProviderRuntimeStatus, The requested canonical Track does not exist., TrackNotFound, DownloadProviderCandidate, NativeMediaInfo, ProviderCandidateFailure, ProviderResolutionResult (+38 more)

### Community 20 - "admin_handlers.py"
Cohesion: 0.08
Nodes (39): Protocol, QueueRuntimeSnapshotReader, Authorized runtime controls over the existing Stage 7 worker settings., Change durable desired state; Stage 7 remains the only pool reconciler., RuntimeWorkerControlService, WorkerControlSnapshot, WorkerMutationResult, WorkerPoolType (+31 more)

### Community 21 - "exceptions.py"
Cohesion: 0.07
Nodes (39): QueueErrorCode, InvalidRequestKeyError, MusicBotError, Exception, QueueFullError, QueueJobNotFoundError, QueueServiceError, Stable application error contract; errors intentionally contain no UI text. (+31 more)

### Community 22 - "UserRole"
Cohesion: 0.10
Nodes (46): UserRole, Container-safe application logging and centralized secret redaction., AdminManagementErrorCode, AdminMutationStatus, StrEnum, Owner-only management of database-backed administrator roles., AdminAccessContext, AdminPermission (+38 more)

### Community 23 - "models/base.py"
Cohesion: 0.08
Nodes (38): Base, Any, datetime, Declarative base and timestamp conventions., Persist UTC and restore timezone awareness on dialects such as SQLite., TimestampMixin, UTCDateTime, BatchDownloadItem (+30 more)

### Community 24 - "Database"
Cohesion: 0.07
Nodes (43): OwnerBootstrapResult, OwnerBootstrapService, StrEnum, Ensure an observed Telegram user matching OWNER_ID has the OWNER role., Database, Any, Connection, Async database lifecycle and isolated SQLite configuration. (+35 more)

### Community 25 - "DownloadFailureCode"
Cohesion: 0.07
Nodes (32): DownloadAttemptStatus, DownloadFailureCode, DownloadAttempt, DownloadPlan, A quality-safe future execution strategy; it never performs media work., _attempt(), _container_extension(), DownloadPipeline (+24 more)

### Community 26 - "BatchDownloadService"
Cohesion: 0.09
Nodes (30): BatchSourceType, Provider-neutral immutable member of an album or playlist snapshot., Immutable provider collection membership returned before persistence., ResolvedCollection, ResolvedCollectionItem, Resolve one immutable album/playlist membership snapshot., ActiveBatchLimitExceeded, BatchDownloadService (+22 more)

### Community 27 - "Settings"
Cohesion: 0.07
Nodes (36): get_settings(), Global download capacity, authoritatively bounded by worker max., Global upload capacity, authoritatively bounded by worker max., Validate Telegram-only settings at the Stage 8 composition boundary., Return validated private HTTP listener settings when enabled., Return cached process settings with an application-level error contract., Runtime settings loaded from environment variables and ``.env``., Settings (+28 more)

### Community 28 - "DeepLinkRegistryService"
Cohesion: 0.08
Nodes (33): DeepLinkNotFound, IdempotencyKeyConflict, InvalidTrackUrl, A recognized provider URL targets an out-of-scope non-track entity., One bot-scoped idempotency key was reused for a different request., A bot-scoped registry token does not exist., UnsupportedMediaType, create_internal_api_app() (+25 more)

### Community 29 - "test_stage134_spotify_authorization.py"
Cohesion: 0.10
Nodes (32): _parse_spotify_webapi_submission(), Accept exactly two bounded lines without ever embedding them in an error., FakeConfig, FakeRequests, FakeResponse, FakeSpotifySession, FakeZeroconfServer, Any (+24 more)

### Community 30 - "test_stage10_admin.py"
Cohesion: 0.11
Nodes (29): QueueRuntimeSnapshot, QueueStatusCounts, SingleFlightSnapshot, SubscriberStatusCounts, TelegramCacheStats, WorkerPoolSnapshot, AdminOverview, AdminOverviewError (+21 more)

### Community 31 - "DownloadJobRepository"
Cohesion: 0.08
Nodes (10): _changed(), DownloadJobRepository, Any, AsyncSession, datetime, SQLite-backed persistent queue operations., Bounded diagnostics for queued requests presently behind a user cap., _rowcount() (+2 more)

### Community 32 - "RuntimeError"
Cohesion: 0.10
Nodes (32): ApplicationInstanceAlreadyRunningError, ApplicationInstanceLock, instance_lock_path(), BaseException, Path, Single-host OS advisory lock for one active SQLite application runtime., Non-blocking lifetime lock; file contents are diagnostics, never authority., sqlite_database_path() (+24 more)

### Community 33 - "repositories/__init__.py"
Cohesion: 0.07
Nodes (18): AsyncSession, Repositories, DownloadProviderAttemptRecord, DownloadProviderCandidateRecord, Focused repository exports., ProviderResolutionRepository, AsyncSession, datetime (+10 more)

### Community 34 - "workers.py"
Cohesion: 0.09
Nodes (19): Protocol, SubscriberLifecycleNotifier, UploadExecutor, WorkerPoolResizer, _artifact_metadata_from_upload(), datetime, Event, Protocol (+11 more)

### Community 35 - "handlers.py"
Cohesion: 0.12
Nodes (42): AlbumActionOutcome, StrEnum, _album_bulk_selection(), _answer_action_failure(), _answer_album_failure(), create_stage9_router(), _edit_or_send(), _invalid_callback() (+34 more)

### Community 36 - "MusicProvider"
Cohesion: 0.07
Nodes (21): The pinned provider boundary cannot reliably resolve this album., UnsupportedAlbum, AlbumSnapshot, AlbumTrackSnapshot, MusicProvider, ABC, MediaReference, Narrow provider contract required by the current metadata stage. (+13 more)

### Community 37 - "composition.py"
Cohesion: 0.09
Nodes (25): Reusable Stage 8 and Stage 9 application composition roots., Stage9Components, _directory_size(), JobDiagnostic, ProviderDiagnostic, Path, Protocol, QueueReader (+17 more)

### Community 38 - "test_stage122_crash_recovery.py"
Cohesion: 0.11
Nodes (28): ArtifactCleanupSummary, Path, Owned periodic task; unexpected termination is observable by supervision., Classify with the exact cleanup policy without deleting anything., StaleArtifactCleanupManager, StaleArtifactCleanupService, is_artifact_job_id(), _cache_row() (+20 more)

### Community 39 - "QualityProfile"
Cohesion: 0.13
Nodes (22): DownloadPlanReadiness, QualityProfile, DownloadPipelineError, Typed terminal Stage 6 failure with safe structured attempt diagnostics., DownloadResult, DownloadPipelineBoundary, _copy_output(), main() (+14 more)

### Community 40 - "test_stage27_production_hardening.py"
Cohesion: 0.09
Nodes (28): AlbumTooLarge, The provider release exceeds the bounded durable snapshot limit., Conservative cleanup of stale, unowned Stage 6 artifact roots., ActiveArtifactRegistry, Process-local protection for artifact roots currently being produced/used., ProviderRateLimiter, ProviderRateLimitSnapshot, Shared, provider-scoped execution pacing for Stage 27. The limiter only delays… (+20 more)

### Community 41 - "TelegramArtifactCacheService"
Cohesion: 0.08
Nodes (18): MediaArtifactSpec, Immutable, deterministic description of requested output bytes., Any, Stable Stage 24 cache identity for one produced Telegram artifact., TelegramCacheKey, datetime, timedelta, Stage 24 cache service; it never authorizes or creates downloads. (+10 more)

### Community 42 - "presentation.py"
Cohesion: 0.13
Nodes (35): AlbumPageCallback, AlbumQualityCallback, AlbumToggleCallback, encode_album_clear_all(), encode_album_download_all(), encode_album_download_selected(), encode_album_first_quality(), encode_album_other_quality() (+27 more)

### Community 43 - "TelegramAlbumRepository"
Cohesion: 0.11
Nodes (9): AlbumItemResolutionStatus, TelegramAlbumRequest, AlbumAggregate, _changed(), Any, AsyncSession, datetime, Atomic persistence operations for Stage 9.3 album orchestration. (+1 more)

### Community 44 - "test_stage133_deezer_authorization.py"
Cohesion: 0.09
Nodes (21): ProviderSecretInput, Opaque credential input whose repr and str are always redacted., Reveal only at the provider backend boundary., SensitiveValue, DeezerArlAuthorizationBoundary, DeezerArlAuthorizationDriver, DeezerArlAuthorizationResult, Protocol (+13 more)

### Community 45 - "OnTheSpotWorker"
Cohesion: 0.11
Nodes (9): _normalize_spotify_webapi_credential(), OnTheSpotWorker, Validate a Developer pair before replacing the two OnTheSpot config keys., Install bounded adapters for provider logins that handle durable secrets., Compatibility entry point retained for focused Stage 13.3 tests., Release pending state only; persisted accounts are never touched., Reload deployment-owned config and rebuild the one serialized runtime pool., Reload durable truth and report only a sanitized startup recovery outcome. (+1 more)

### Community 46 - "test_stage9_telegram.py"
Cohesion: 0.13
Nodes (24): StrEnum, QualitySelectionResult, TelegramTrackRequestService, TrackRequestActionOutcome, TrackRequestActionResult, _artifact_metadata(), _drain(), Gateway (+16 more)

### Community 47 - "TrackReference"
Cohesion: 0.09
Nodes (20): AlbumReference, PlaylistReference, TrackReference, _album_snapshot(), _bounded_album_text(), _duration_ms(), _normalized_track_metadata(), _optional_bool() (+12 more)

### Community 48 - "test_stage103_runtime_worker_controls.py"
Cohesion: 0.11
Nodes (28): AdminOverviewService, Freshly authorize, then compose bounded local statistics only., StrEnum, WorkerMutationStatus, Telegram user observation and durable preference service., TelegramUserService, AdminHandlerDependencies, create_admin_router() (+20 more)

### Community 49 - "TelegramDeliveryStatus"
Cohesion: 0.11
Nodes (9): TelegramDeliveryStatus, _changed(), Any, AsyncSession, datetime, Atomic persistence operations for the Stage 9 delivery outbox., Move a terminal presentation reference after one replacement send., Bounded recent requests with a durable user-visible status message. (+1 more)

### Community 50 - "LocalizationService"
Cohesion: 0.12
Nodes (21): LocalizationError, LocalizationFormatError, A locale catalog or translation template is invalid., A translation could not be formatted with the supplied values., Localization services., _format_fields(), LocalizationService, Any (+13 more)

### Community 51 - "OperationalAuditService"
Cohesion: 0.14
Nodes (16): DeepLinkTargetType, OperationalAuditActorKind, OperationalAuditEventType, OperationalAuditTargetKind, AuditEventView, OperationalAuditService, Path, Validated, bounded builders for high-value operational audit history. (+8 more)

### Community 52 - "AdminManagementPresentation"
Cohesion: 0.20
Nodes (19): AdministratorPage, ManagedUser, PromotionCandidatePage, AdminManagementCallback, AdminManagementCallbackAction, AdminManagementPresentation, _bounded_int(), encode_admin_management_callback() (+11 more)

### Community 53 - "test_stage28_collection_presentation.py"
Cohesion: 0.15
Nodes (25): BatchStatus, CollectionStatusPresentationService, datetime, Best-effort Stage 28 parent presentation over authoritative Stage 23 aggregates., Adapt the batch aggregate to the shared edit policy vocabulary., _status_view(), _text(), datetime (+17 more)

### Community 54 - "worker.py"
Cohesion: 0.10
Nodes (22): Private JSON Lines protocol shared by the OnTheSpot parent and worker., _atomic_onthespot_config_writes(), _atomic_write_private_json(), _close_pinned_zeroconf_server(), _is_local_ipv4_address(), main(), Path, Isolated OnTheSpot JSON Lines worker. This module intentionally sets… (+14 more)

### Community 55 - "ResolveTrackService"
Cohesion: 0.15
Nodes (12): NormalizedTrackMetadata, ProviderDiscoveryResult, Identity of one specific recording/version, independent of provider., TrackIdentity, TrackMatchResult, Resolve one track from stable provider identity., Resolve and normalize metadata for one track URL., ResolveTrackAdapter (+4 more)

### Community 56 - "TelegramPresentation"
Cohesion: 0.14
Nodes (10): BatchProgress, BatchHistoryEntry, AlbumCard, AlbumSelectionPage, _card(), _bounded(), _format_duration(), InlineKeyboardMarkup (+2 more)

### Community 57 - "PreparedSourceMedia"
Cohesion: 0.26
Nodes (21): DownloadPlanOperation, PreparedSourceMedia, Normalized facts about provider-native media owned by one Stage 6 job., FakeProbe, FakeProvider, FakeTranscoder, _job_directories(), _pipeline() (+13 more)

### Community 58 - "ProviderAccountsPresentation"
Cohesion: 0.17
Nodes (15): _bounded(), encode_provider_accounts_callback(), parse_provider_accounts_callback(), ProviderAccountsCallback, ProviderAccountsCallbackAction, ProviderAccountsPresentation, InlineKeyboardMarkup, StrEnum (+7 more)

### Community 59 - "test_stage121_runtime_hardening.py"
Cohesion: 0.09
Nodes (20): Return log-safe text while preserving ordinary public/job identifiers., Format one-line text logs, including safe async-flow identifiers., redact_secrets(), RedactingFormatter, _executable_available(), Path, Validate fatal local prerequisites without network access or socket binding., RuntimePrerequisiteReport (+12 more)

### Community 60 - "TidalDeviceAuthorizationDriver"
Cohesion: 0.09
Nodes (12): _failed(), Exception, Protocol, Poll one bounded child operation at a time and verify runtime truth on success., TidalAuthorizationDriverError, TidalDeviceAuthorizationBoundary, TidalDeviceAuthorizationDriver, TidalDeviceAuthorizationPoll (+4 more)

### Community 61 - "unit/test_stage132_tidal_authorization.py"
Cohesion: 0.18
Nodes (19): _device_response(), FakeConfig, FakeRequests, FakeResponse, Any, Exception, MonkeyPatch, parametrize (+11 more)

### Community 62 - "_worker_with_deezer"
Cohesion: 0.14
Nodes (18): _anonymous_payload(), FakeConfig, FakeRequests, FakeResponse, FakeSession, Any, Exception, MonkeyPatch (+10 more)

### Community 63 - "enums.py"
Cohesion: 0.14
Nodes (19): DownloadPlanReason, ProviderAttemptStatus, StrEnum, QualityCandidateRejectionReason, QualityResolutionStatus, Provider-independent domain enumerations., SourceValidationConfidence, TelegramDeliveryErrorCode (+11 more)

### Community 64 - "BatchDownloadRepository"
Cohesion: 0.12
Nodes (9): BatchItemStatus, BatchDownloadRequest, BatchDownloadRepository, AsyncSession, datetime, Atomic persistence operations for Stage 23 batches., Persist metadata and every member in one transaction., Atomically reserve one item for admission. The reservation uses the existing… (+1 more)

### Community 65 - "User"
Cohesion: 0.11
Nodes (4): Compatibility alias for the pre-Stage-9 internal field name., User, AsyncSession, UserRepository

### Community 66 - "track_identity.py"
Cohesion: 0.15
Nodes (17): _extract_feature(), _extract_version(), identity_from_metadata(), identity_from_values(), _markers_in(), normalize_duration_ms(), normalize_isrc(), normalize_text() (+9 more)

### Community 67 - "ops.py"
Cohesion: 0.18
Nodes (24): require_current_schema(), _schema_revision(), ArtifactCleanupAuditDetails, _add_json(), _audit_list(), _backup(), _directory_size(), _dispatch() (+16 more)

### Community 68 - "test_stage93_album_integration.py"
Cohesion: 0.34
Nodes (22): AlbumRequestStatus, _album_service(), AlbumResolver, _artifact(), _coordinator(), _drain_album(), Gateway, Event (+14 more)

### Community 69 - "UnsupportedProvider"
Cohesion: 0.16
Nodes (17): UnsupportedProvider, FakeProcessClient, _provider(), Any, asyncio, parametrize, test_detects_and_canonicalizes_supported_track_url(), test_passes_only_canonical_url_and_maps_safe_metadata() (+9 more)

### Community 70 - "repositories/singleflight.py"
Cohesion: 0.15
Nodes (11): legacy_quality_fingerprint(), Any, Stable identity for pre-Stage-26 queue callers., DownloadFlight, Ephemeral durable ownership of one active Track + QualityProfile pipeline., AdmissionRecord, CancellationRecord, AsyncSession (+3 more)

### Community 71 - "test_stage9_callbacks.py"
Cohesion: 0.14
Nodes (23): Track, _track_card(), TrackCard, encode_first_quality(), encode_locale(), encode_other_quality(), encode_setting_quality(), encode_track_back() (+15 more)

### Community 72 - "What You Must Do When Invoked"
Cohesion: 0.08
Nodes (24): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+16 more)

### Community 73 - "test_stage134_spotify_telegram.py"
Cohesion: 0.20
Nodes (18): _bot_side_effect(), CacheStatsFake, CaptureDriver, _create_user(), _message_update(), PlaybackDriver, Any, Bot (+10 more)

### Community 74 - "test_resolution_service.py"
Cohesion: 0.25
Nodes (19): ProviderDiscoveryStatus, TrackSearchCandidate, Return a bounded set of lightweight track candidates., _metadata(), Any, asyncio, StubProvider, test_ambiguous_discovery_candidate_is_not_persisted() (+11 more)

### Community 75 - "ProviderAvailability"
Cohesion: 0.09
Nodes (4): ProviderAvailability, Report whether the adapter dependency can be initialized., MutableRuntimeProvider, RuntimeSourceProvider

### Community 76 - "config.py"
Cohesion: 0.12
Nodes (19): do_run_migrations(), include_object(), Async Alembic migration environment., run_async_migrations(), run_migrations_offline(), _empty_to_none(), _normalize_locale(), _normalize_log_level() (+11 more)

### Community 77 - "TelegramAlbumRequestService"
Cohesion: 0.19
Nodes (6): AlbumResolutionFailed, A recognized album could not be resolved through its provider boundary., AlbumActionResult, TelegramAlbumRequestService, MediaAdmission, TelegramMediaRequestService

### Community 78 - "core/quality.py"
Cohesion: 0.19
Nodes (20): _is_lossless(), _plan_for_media(), plan_sort_key(), plans_for_candidate(), Provider-neutral quality policy for future download planning., Rank semantic quality/certainty first, then stable neutral identities., Build every safe strategy for one currently available source., Return one deterministic, machine-readable explanation for no plan. (+12 more)

### Community 79 - "download_activity.py"
Cohesion: 0.14
Nodes (15): DownloadActivity, DownloadActivityService, _failure_code(), Owner-scoped, read-only Stage 28 activity projection over the lifecycle., Never exposes provider attempts, filesystem details, or another user's work., _detail_keyboard(), _detail_text(), _downloads_keyboard() (+7 more)

### Community 80 - "test_onthespot_worker.py"
Cohesion: 0.17
Nodes (18): FakeAccounts, FakeRegistry, Any, MonkeyPatch, parametrize, Path, test_authenticated_provider_without_active_account_requires_auth(), test_available_source_returns_only_normalized_facts() (+10 more)

### Community 81 - "test_stage11_internal_api.py"
Cohesion: 0.23
Nodes (20): DeepLinkStatus, AsyncClient, _auth(), _client(), LogCaptureFixture, SimpleNamespace, _service(), Stage11Provider (+12 more)

### Community 82 - "Any"
Cohesion: 0.16
Nodes (13): _account_bitrate(), _health_result(), _native_media(), Any, Inspect selected native media without returning URLs, manifests, or credentials., Return only normalized readiness facts; never return account or token data., Inspect current runtime account/session facts without selecting a TrackSource., Return independent sanitized playback and Web API readiness facts. (+5 more)

### Community 83 - "TelegramDeliveryRequest"
Cohesion: 0.24
Nodes (5): Best-effort origin-chat guidance for an unreachable USER target., Derive technical identity only from the admitted immutable request., TelegramDeliveryWorker, _quality_profile_for_preference(), TelegramDeliveryRequest

### Community 84 - "Stage 26 — Metadata & Media Processing"
Cohesion: 0.09
Nodes (21): 10. Errors, retries, and ownership, 11. Collection behavior, 12. Acceptance matrix, 13. Schema decision, 14. Minimal implementation plan, 15. Required Stage 26 acceptance tests, 16. Regression boundaries, 17. Required production-level proofs (+13 more)

### Community 85 - "test_config.py"
Cohesion: 0.21
Nodes (20): make_settings(), MonkeyPatch, parametrize, test_application_log_level_is_normalized(), test_default_locale_must_be_supported(), test_empty_owner_id_remains_optional(), test_enabled_internal_api_requires_a_strong_trimmed_token(), test_enabled_internal_api_returns_validated_listener_configuration() (+12 more)

### Community 86 - "WorkerError"
Cohesion: 0.18
Nodes (8): _album_positive_int(), _album_text(), _download_media(), Exception, Run only pinned service download/decryption code; skip all quality post-…, _safe_native_extension(), _silence_upstream, WorkerError

### Community 87 - "utc_now"
Cohesion: 0.18
Nodes (6): Event, Restart-safe expansion of selected album positions into ordinary deliveries., TelegramAlbumCoordinator, TelegramAlbumCoordinatorManager, utc_now(), TelegramAlbumItem

### Community 88 - "Musicbot Downloader"
Cohesion: 0.10
Nodes (20): Architecture, Current limitations, Database migrations, Development checks, Download tool, Internal API and Telegram deep links (Stage 11), Internationalization, Live Provider Health (+12 more)

### Community 89 - "test_stage133_deezer_telegram.py"
Cohesion: 0.25
Nodes (16): _bot_side_effect(), CacheStatsFake, CaptureDriver, _create_user(), _message_update(), Any, Bot, ChatType (+8 more)

### Community 90 - "_pinned_worker"
Cohesion: 0.21
Nodes (16): _pinned_worker(), PinnedConfig, Any, LogCaptureFixture, MonkeyPatch, parametrize, Path, test_atomic_update_replaces_whole_config_with_restrictive_permissions() (+8 more)

### Community 91 - "AdministratorManagementService"
Cohesion: 0.28
Nodes (6): AdministratorManagementService, AdminManagementError, AdminMutationResult, _managed(), _page_bounds(), Authorize every read/change and expose only Stage 10.2 transitions.

### Community 92 - "test_stage123_ops.py"
Cohesion: 0.26
Nodes (13): Path, Validated SQLite online backup with an atomic, non-overwriting destination., SQLiteBackupResult, SQLiteBackupService, _current_migration_head(), _migrate(), MonkeyPatch, Path (+5 more)

### Community 93 - "test_stage11_telegram_deep_links.py"
Cohesion: 0.24
Nodes (14): CacheGateway, _message_update(), Provider, Message, SimpleNamespace, Update, RegistrationResolver, _sent_message() (+6 more)

### Community 94 - "test_track_identity.py"
Cohesion: 0.30
Nodes (17): TrackMatchDecision, match_track_candidates(), match_track_identities(), _identity(), Any, parametrize, test_different_valid_isrc_blocks_title_similarity(), test_duplicate_isrc_selects_only_uniquely_compatible_candidate() (+9 more)

### Community 95 - "_AtomicReplaceTextFile"
Cohesion: 0.14
Nodes (6): _AtomicReplaceTextFile, _deezer_failure(), _DeezerValidatedSession, _normalize_deezer_arl(), _parse_deezer_user_data(), Validate through HTTPS before writing an OnTheSpot-owned account record.

### Community 96 - ".quality_profile"
Cohesion: 0.12
Nodes (7): upgrade(), upgrade(), upgrade(), downgrade(), upgrade(), upgrade(), Map the neutral quality tier to the existing Stage 21 pipeline contract.

### Community 97 - "WorkerSettingsService"
Cohesion: 0.32
Nodes (4): WorkerSettingsSnapshot, WorkerSettingValues, WorkerSettingMutation, WorkerSettingsService

### Community 98 - "README.md"
Cohesion: 0.14
Nodes (9): OnTheSpot v1.8.1 provider capability matrix, Runtime interpretation, Stage 10.4 provider-level health audit, Stage 6 native-download audit, Stage 9.3 album snapshot audit, Architecture and authority, Explicit non-goals, Stage 13.1 — Provider Account Management Foundation (+1 more)

### Community 99 - "track_resolution.py"
Cohesion: 0.18
Nodes (9): DatabaseConcurrencyError, DatabaseError, A transient write conflict that may be retried as a whole transaction., A provider identity is already owned by a different canonical Track., TrackSourceOwnershipConflict, Adapter from Stage 17 catalog tracks to the existing canonical Track resolver., Preserves Stage 3 identity persistence before Stage 6 queue admission., RecognizedTrackResolutionAdapter (+1 more)

### Community 100 - "crash_recovery.py"
Cohesion: 0.24
Nodes (8): CrashRecoveryService, CrashRecoverySummary, datetime, Deterministic, side-effect-free startup reconciliation., Compose existing durable recovery primitives before any worker claims., Return a read-only snapshot using the same recovery predicates., RecoveryInspection, RecoveryAuditDetails

### Community 101 - "AsyncWorkerPool"
Cohesion: 0.23
Nodes (3): AsyncWorkerPool, Task, _WorkerSlot

### Community 102 - "InternalApiServer"
Cohesion: 0.21
Nodes (6): _EmbeddedUvicornServer, InternalApiServer, FastAPI, Embedded non-blocking uvicorn lifecycle., Leave process signals under the application-level supervisor., Wait for unexpected listener termination so the process supervisor can fail.

### Community 103 - ".tidal_device_authorization_start"
Cohesion: 0.18
Nodes (8): _bounded_number(), Create one bounded Tidal device challenge without exposing its device code., Perform at most one token-endpoint request for an existing device flow., _tidal_account_from_token_payload(), _tidal_poll_result(), _tidal_start_failure(), _TidalDeviceFlow, _valid_tidal_verification_url()

### Community 105 - "DeepLinkRegistryEntry"
Cohesion: 0.29
Nodes (5): DeepLinkRegistryEntry, DeepLinkRegistryRepository, AsyncSession, datetime, Focused persistence operations for the deep-link registry.

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

### Community 112 - "track_matching.py"
Cohesion: 0.44
Nodes (9): TrackEvidenceCode, TrackMatchCandidate, TrackMatchEvidence, _compare_text(), _duration_detail(), _duration_evidence(), _evaluate(), Explainable deterministic rules for deciding recording identity. (+1 more)

### Community 113 - "download_history.py"
Cohesion: 0.33
Nodes (6): decode_history_cursor(), encode_history_cursor(), HistoryPage, datetime, Projection and exact replay service for Stage 24 history., TrackHistoryEntry

### Community 114 - "UserDownloadPreferencesRecord"
Cohesion: 0.33
Nodes (5): UserDownloadPreferencesRecord, AsyncSession, datetime, Persistence adapter for Stage 22 user preferences., UserDownloadPreferencesRepository

### Community 115 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 116 - "Stage 27 — Production Limits, Resource Safety & Observability"
Cohesion: 0.22
Nodes (8): Acceptance and non-goals, Authority map, Configuration and policies, Diagnostics, Goal and boundary, Recovery and lease invariant, Stage 27 — Production Limits, Resource Safety & Observability, Storage pressure

### Community 117 - "ProviderAccountRuntimeProbe"
Cohesion: 0.25
Nodes (3): ProviderAccountRuntimeProbe, Protocol, Sanitized child/runtime boundary reused by account management.

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

### Community 123 - "TelegramStatusPresentationService"
Cohesion: 0.33
Nodes (4): _failure_code(), Best-effort Telegram rendering; lifecycle correctness never depends on it., Best-effort bounded repair after durable recovery has completed., TelegramStatusPresentationService

### Community 124 - "resolve.py"
Cohesion: 0.48
Nodes (6): _duration(), _known(), main(), _present(), Resolve and persist one track URL through the provider boundary., _run()

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
Cohesion: 0.29
Nodes (6): Architecture, Deterministic scoring and ranking, Domain models, Replacement and compatibility seams, Stage 17 — Track Recognition, Tests

### Community 130 - "Stage 18 — Download Flow UX"
Cohesion: 0.29
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

### Community 136 - ".__init__"
Cohesion: 0.40
Nodes (3): Event, Protocol, TelegramTrackResolver

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
- **191 isolated node(s):** `musicbot-downloader`, `Usage`, `What graphify is for`, `Step 0 - GitHub repos and multi-path merge (only if a URL or several paths)`, `Step 1 - Ensure graphify is installed` (+186 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 1173 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **19 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `MusicProviderName` connect `MusicProviderName` to `Track`, `compose_stage9`, `DownloadArtifactManager`, `stage25_execution.py`, `unit/test_stage17_track_recognition.py`, `OnTheSpotProvider`, `test_stage8_telegram_cache.py`, `DownloadLifecycleService`, `UserDownloadPreferences`, `ProviderAuthorizationOutcome`, `test_onthespot_external.py`, `TelegramContext`, `ProviderUnavailable`, `SingleFlightService`, `NativeCodec`, `ProviderHealthService`, `ProviderRuntimeStatus`, `admin_handlers.py`, `UserRole`, `models/base.py`, `Database`, `DownloadFailureCode`, `BatchDownloadService`, `Settings`, `DeepLinkRegistryService`, `test_stage134_spotify_authorization.py`, `test_stage10_admin.py`, `RuntimeError`, `repositories/__init__.py`, `workers.py`, `MusicProvider`, `composition.py`, `test_stage122_crash_recovery.py`, `QualityProfile`, `test_stage27_production_hardening.py`, `presentation.py`, `TelegramAlbumRepository`, `test_stage133_deezer_authorization.py`, `OnTheSpotWorker`, `test_stage9_telegram.py`, `TrackReference`, `test_stage103_runtime_worker_controls.py`, `test_stage28_collection_presentation.py`, `worker.py`, `ResolveTrackService`, `PreparedSourceMedia`, `ProviderAccountsPresentation`, `TidalDeviceAuthorizationDriver`, `unit/test_stage132_tidal_authorization.py`, `enums.py`, `BatchDownloadRepository`, `User`, `test_stage93_album_integration.py`, `UnsupportedProvider`, `test_stage134_spotify_telegram.py`, `test_resolution_service.py`, `ProviderAvailability`, `TelegramAlbumRequestService`, `core/quality.py`, `test_stage11_internal_api.py`, `Any`, `test_stage133_deezer_telegram.py`, `test_stage11_telegram_deep_links.py`, `test_track_identity.py`, `track_resolution.py`, `DeepLinkRegistryEntry`, `download_history.py`, `ProviderAccountRuntimeProbe`?**
  _High betweenness centrality (0.196) - this node is a cross-community bridge._
- **Why does `QualityProfile` connect `QualityProfile` to `Track`, `DownloadArtifactManager`, `stage25_execution.py`, `test_stage8_telegram_cache.py`, `UserDownloadPreferences`, `TelegramContext`, `SingleFlightService`, `NativeCodec`, `ProviderRuntimeStatus`, `exceptions.py`, `UserRole`, `models/base.py`, `Database`, `DownloadFailureCode`, `test_stage10_admin.py`, `DownloadJobRepository`, `workers.py`, `test_stage122_crash_recovery.py`, `test_stage27_production_hardening.py`, `presentation.py`, `TelegramAlbumRepository`, `test_stage9_telegram.py`, `test_stage103_runtime_worker_controls.py`, `TelegramDeliveryStatus`, `TelegramPresentation`, `PreparedSourceMedia`, `enums.py`, `User`, `test_stage93_album_integration.py`, `repositories/singleflight.py`, `test_stage9_callbacks.py`, `config.py`, `TelegramAlbumRequestService`, `core/quality.py`, `TelegramDeliveryRequest`, `test_stage11_telegram_deep_links.py`, `.quality_profile`?**
  _High betweenness centrality (0.058) - this node is a cross-community bridge._
- **Why does `Database` connect `Database` to `Track`, `compose_stage9`, `DownloadArtifactManager`, `stage25_execution.py`, `test_stage8_telegram_cache.py`, `DownloadLifecycleService`, `UserDownloadPreferences`, `.__init__`, `TelegramContext`, `ProviderAuthorizationOutcome`, `SingleFlightService`, `ProviderRuntimeStatus`, `exceptions.py`, `UserRole`, `DownloadFailureCode`, `BatchDownloadService`, `Settings`, `test_stage10_admin.py`, `repositories/__init__.py`, `workers.py`, `MusicProvider`, `composition.py`, `test_stage122_crash_recovery.py`, `QualityProfile`, `TelegramArtifactCacheService`, `test_stage9_telegram.py`, `test_stage103_runtime_worker_controls.py`, `OperationalAuditService`, `test_stage28_collection_presentation.py`, `ResolveTrackService`, `PreparedSourceMedia`, `enums.py`, `ops.py`, `test_stage93_album_integration.py`, `test_stage9_callbacks.py`, `test_stage134_spotify_telegram.py`, `test_resolution_service.py`, `config.py`, `download_activity.py`, `test_stage11_internal_api.py`, `utc_now`, `test_stage133_deezer_telegram.py`, `AdministratorManagementService`, `test_stage123_ops.py`, `test_stage11_telegram_deep_links.py`, `track_resolution.py`, `crash_recovery.py`, `TelegramStatusPresentationService`, `resolve.py`?**
  _High betweenness centrality (0.046) - this node is a cross-community bridge._
- **Are the 304 inferred relationships involving `MusicProviderName` (e.g. with `compose_stage9()` and `AlbumSnapshot`) actually correct?**
  _`MusicProviderName` has 304 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `Database` (e.g. with `OwnerBootstrapService` and `ResolveTrackService`) actually correct?**
  _`Database` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 193 inferred relationships involving `QualityProfile` (e.g. with `DownloadOptions` and `EffectiveDownloadProfile`) actually correct?**
  _`QualityProfile` has 193 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `LocalizationService` (e.g. with `LocalizationError` and `LocalizationFormatError`) actually correct?**
  _`LocalizationService` has 2 INFERRED edges - model-reasoned connections that need verification._