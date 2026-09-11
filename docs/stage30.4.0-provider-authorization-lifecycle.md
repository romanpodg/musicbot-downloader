# Stage 30.4.0 — Provider Authorization Lifecycle

> Historical Stage 30.4.0 snapshot. For the current managed-provider set,
> including Qobuz and Apple Music, see
> [`stage30-provider-platform.md`](stage30-provider-platform.md).

Status: **COMPLETE**

## Why this boundary changed

`ProviderAuthorizationMethod` remains the safe description of credential/input
shape (browser pairing, one secret, compound credentials, or Qobuz's legacy
email/password path). It is not sufficient to describe what happens after
input is collected: providers differ in session validation, refresh, revoke,
entitlement, and readiness behavior.

## Lifecycle model

`AuthorizationCapabilities` advertises an explicit, optional subset of the
provider-neutral lifecycle operations: configure, validate, authenticate,
refresh, revoke, reset, entitlement check, session validation, and readiness
projection. The coordinator exposes capability inspection and a fail-closed
extension seam for optional operations. Existing `start`/`wait`/submission APIs
are unchanged.

Current mappings:

| Provider flow | Lifecycle facts |
| --- | --- |
| Spotify playback pairing | authenticate, validate, session validation, readiness projection |
| Spotify Web API credentials | configure, authenticate, validate, session validation, readiness projection |
| Tidal device authorization | authenticate, validate, session validation, readiness projection |
| Deezer ARL | configure, authenticate, validate, session validation, readiness projection |
| Qobuz email/password | configure, authenticate, validate, session validation, readiness projection |

Qobuz keeps `QOBUZ_CREDENTIALS` for compatibility; its provider-specific
driver remains the owner of login and session verification.

## Boundaries and compatibility

The main process accepts intent and sanitized DTOs. The isolated child runtime
continues to own credentials, sessions, authentication, persistence, and
health evidence. No credential or token is stored in SQLite, application
models, callbacks, logs, or audit records.

The explicit composition driver manifest and `ProviderIntegrationRegistry` are
unchanged. Spotify, Tidal, Deezer, Qobuz, and YouTube Music behavior remain on
their existing paths. Apple Music remains deferred and is not activated.
