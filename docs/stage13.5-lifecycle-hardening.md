# Stage 13.5 — Provider Lifecycle, Security, and Crash Hardening

Stage 13.5 completes Provider Account Management without adding providers, authentication methods,
account switching, credential export, or Telegram track search. Tidal device authorization, Deezer
ARL input, Spotify playback pairing, and Spotify Developer credentials remain separate drivers under
the accepted Stage 13.1 coordinator.

## Credential ownership and lifecycle

Credentials remain owned by the isolated OnTheSpot child and `/data/onthespot/otsconfig.json`.
SQLite, application JSON, callbacks, DTOs, audit events, logs, Telegram output, and normal database
backups contain no provider credentials or pending authorization flows.

The sanitized provider-account lifecycle is:

```text
NOT_CONFIGURED -> AUTHORIZING -> READY
                       |           |
                       v           v
                 INVALID/ERROR  DEGRADED/EXPIRED/REVOKED
                                      |
                                      v
                                  RECOVERING
```

These states normalize lifecycle meaning; they do not make authentication behavior identical.
`READY` requires durable configuration, an accepted runtime session, and the provider's required
runtime capability. A file or successful earlier authorization is not sufficient.

Spotify retains independent `PLAYBACK` and `WEB_API` components. Playback remains the download
state. A configured Web API component whose operation is rate-limited, quota-exceeded, forbidden,
or otherwise degraded makes an otherwise ready Spotify account report overall `DEGRADED`; a Web
API component that was never configured does not prevent playback readiness.

## Startup reconciliation and child recovery

Startup performs provider lifecycle reconciliation after general crash-artifact cleanup and before
queue workers start. The child reloads durable OnTheSpot configuration, rebuilds runtime sessions,
revalidates Spotify Developer credentials, applies bounded Tidal refresh when needed, and deletes
crash-left Spotify pairing files and incomplete atomic-config siblings. Configured credentials with
an unavailable session are reported as degraded or recovering, never ready. Missing configuration
becomes `NOT_CONFIGURED`; invalid, expired, and revoked facts remain sanitized.

An IPC timeout, disconnect, malformed frame, or child crash fails the current request and terminates
that child generation. The request is not replayed, because persistence may already have succeeded.
The next request starts a fresh child, reloads durable truth, and recalculates readiness. Parent
authorization tasks finish with a sanitized failure and the coordinator releases their generation;
shutdown cancels coordinator/UI tasks before closing the child.

## Atomic updates and reset

Every Stage 13 credential mutation follows:

```text
validate -> prepare complete config -> write restrictive sibling -> fsync -> atomic replace
         -> reload runtime -> verify readiness
```

Validation or persistence failure preserves the previous file and in-memory values. A reload or
verification failure preserves the newly persisted credential but does not report `READY`; restart
reconciliation retries from durable truth. Reauthorizing the same Tidal identity replaces its
validated record instead of appending a duplicate. Spotify Client Credentials invalidate the
child-only OAuth cache only after atomic persistence.

The OWNER-only private Provider Accounts UI provides Refresh, sanitized status diagnostics, and a
confirmed Reset authentication action. Reset cancels an active provider flow when safe, atomically
removes only the selected provider's `accounts[]` records and provider-specific keys, rebuilds the
runtime, and verifies `NOT_CONFIGURED`. Spotify reset removes both playback and Web API state.
Unrelated providers are preserved. Reset is not account switching, migration, sharing, or export.

## Secret and filesystem guarantees

Sensitive Telegram input is still deleted before `SensitiveValue` construction and child handoff.
Secrets never enter callback data or public DTO fields; raw child stderr is discarded; IPC and
provider exceptions are normalized. The OnTheSpot directory is restricted to `0700` and its config
and atomic temporary files to `0600` on POSIX. The Spotify pairing directory is `0700`, its generated
credential file is restricted before reading, and every success, cancellation, error, shutdown, and
restart path removes it. Windows host mode bits are not POSIX acceptance evidence; the Linux image
filesystem checks are authoritative.

Provider Health is observational. It reads the current child runtime and does not reload accounts,
refresh credentials, create sessions, or modify configuration. Credential reload and mutation occur
only in startup reconciliation, explicit Provider Accounts Refresh, authorization, or reset.

## Backup, restore, and external limits

The normal SQLite backup continues to exclude `/data/onthespot`. Provider configuration must be
backed up separately through a protected operator process while the application is stopped. Restore
must not merge `accounts[]` records or copy live temporary pairing artifacts. After a provider-config
restore, start exactly one application instance; startup reconciliation reloads the restored file and
prevents stale in-memory `READY` state.

Deterministic tests use fake credentials and provider responses. Real Tidal, Deezer, Spotify pairing,
Spotify Web API, Telegram deletion, LAN Zeroconf, and provider-side revocation remain optional
external smoke checks and must be reported separately. Stage 13 does not provide search UI,
automatic recognition, a provider-account marketplace, or multi-account management.
