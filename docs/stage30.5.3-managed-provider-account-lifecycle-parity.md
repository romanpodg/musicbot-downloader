# Stage 30.5.3 — Managed Provider Account Lifecycle Parity

Stage 30.5.3 closes managed-account lifecycle gaps for Spotify, Deezer, Tidal,
Qobuz, and Apple Music.

## Apple reset boundary

The isolated worker already implemented provider-scoped Apple reset, but
`OnTheSpotProcessClient` rejected `apple_music` before sending the IPC request.
The minimal correction admits Apple in the existing explicit managed reset
allowlist. The IPC method and wire format are unchanged; unmanaged and deferred
providers remain fail-closed.

## Reset semantics

Reset is local application/runtime cleanup: child-owned provider configuration
and active session state are removed, the runtime pool is rebuilt, and health is
reconciled. It is not a claim of provider-global credential revocation. A
successful reset normally yields `AUTH_REQUIRED`/`AUTH_NOT_CONFIGURED`, not a
generic error.

Qobuz follows the same existing provider-scoped contract. Apple and Qobuz reset
operations leave other providers untouched.

## Restart reconciliation

Startup reconciliation reloads child-owned state and uses the existing provider
validation paths. Configuration presence alone never implies `READY`:

- Apple requires a valid session and premium subscription; invalid sessions are
  sanitized to non-ready state and missing premium access maps to
  `SUBSCRIPTION_REQUIRED`.
- Qobuz requires authenticated catalog/session verification; stale or invalid
  state becomes non-ready without aborting application startup.

Failures remain isolated to the affected provider while Spotify, Deezer, Tidal,
and the other managed provider continue reconciliation.

## Security and scope

Apple media-user tokens and Qobuz credentials/session material remain owned by
the child runtime and are not persisted in application SQLite or returned over
sanitized IPC/health DTOs. OWNER authorization remains enforced by the existing
provider-account administration flow.

YouTube Music remains unmanaged; Bandcamp and SoundCloud remain deferred. No
authorization methods, capability model, provider registry order, schema,
dependency, runtime pin, queue, cache, recognition, or quality behavior changed.
