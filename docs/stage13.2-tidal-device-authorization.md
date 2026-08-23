# Stage 13.2 — Tidal Device Authorization

Stage 13.2 adds the first real Provider Accounts authorization driver:

```text
authoritative OWNER
  → private Telegram Provider Accounts / Tidal / Connect
  → Tidal browser device authorization
  → bounded automatic polling in the isolated OnTheSpot child
  → OnTheSpot configuration persistence
  → OnTheSpot runtime account reload and verification
  → READY
```

The Telegram message contains an ordinary URL button and an opaque, generation-bound Cancel
callback. The device code, token response, account dictionary, access token, and refresh token
remain inside the child process. Each poll RPC performs at most one token-endpoint HTTP request,
uses a bounded network timeout, and returns control before the next interval. Normal metadata,
health, and download requests can therefore use the existing serialized child between polls.

Successful credentials use the exact account structure required by pinned OnTheSpot v1.8.1 and
are saved through OnTheSpot's configuration object. SQLite is not a credential source of truth and
contains no Tidal token, provider-account table, pending-flow row, or synthetic READY flag. After
persistence, the application invokes the existing runtime reload boundary and reports READY only
when the rebuilt runtime account pool recognizes Tidal as usable. A reload failure leaves the
OnTheSpot-owned account intact so Refresh or a later restart can recover it.

Pending authorization state is process-local and is intentionally not recovered after restart.
There is at most one active flow per provider. Cancellation and shutdown release child state and
tracked coordinator/UI tasks; a stale flow-generation callback cannot cancel a newer flow.

Only Tidal browser/device authorization is implemented in Stage 13.2. Deezer ARL entry remains
Stage 13.3, Spotify playback/search credentials remain Stage 13.4, and broader credential
lifecycle, replacement/disconnect, security, and crash hardening remain Stage 13.5. Stage 13 as a
whole is not complete.
