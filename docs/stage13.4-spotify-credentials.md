# Stage 13.4 — Spotify Playback and Search Credentials

Stage 13.4 adds two independent OWNER-only Spotify configurations to the accepted Stage 13.1
coordinator. It does not treat them as one authentication flow.

## Component readiness

The Spotify account detail reports `PLAYBACK` and `WEB_API` independently. Playback uses `READY`,
`AUTH_REQUIRED`, `AUTHORIZING`, or `ERROR`. Web API uses `READY`, `NOT_CONFIGURED`, `AUTHORIZING`,
or `ERROR`, plus `AVAILABLE`, `RATE_LIMITED`, `QUOTA_EXCEEDED`, `FORBIDDEN`, or `UNKNOWN` as a
separate operational observation.

The generic Stage 13.1 Spotify state remains the playback/download state. A ready Developer pair
does not make unpaired playback ready, while future catalog code can inspect Web API readiness
without playback. Values merely present in `otsconfig.json` are not sufficient for Web API
`READY`; startup and explicit Refresh revalidate them inside the child.

## Playback pairing

The exact pinned runtime is OnTheSpot commit
`8ed6cf33ef772e6569d5014237e0fb4ce8b9e45d`, resolving librespot 0.0.10 commit
`4e70bc40b7a64f522a90c9e9026326ffa9d1580c`. The pinned `spotify_new_session()` waits forever and
is not called. Three bounded child RPCs replace it:

```text
OWNER / Spotify / Connect Playback
  → child creates one temporary credential path
  → child starts one librespot Zeroconf server and returns immediately
  → owner opens Spotify → Devices → OnTheSpot
  → each child poll inspects has_valid_session() exactly once
  → child requires account type premium
  → child reads the temporary stored credential only inside the child
  → child appends the pinned OnTheSpot account schema once and saves config
  → child closes discovery and removes the temporary pairing file
  → existing runtime reload rebuilds accounts
  → runtime health must verify a Premium Spotify session
  → playback becomes READY
```

The credential blob, username, session object, and session path never enter a parent DTO, callback,
Telegram message, audit payload, application log, environment variable, or SQLite. OnTheSpot owns
the durable `accounts[]` record. Its normal `ots_login_<uuid>.json` runtime artifact is retained;
only the pairing-generation temporary file is removed.

Free accounts return `SPOTIFY_PLAYBACK_PREMIUM_REQUIRED`. Unknown types fail closed with
`SPOTIFY_PLAYBACK_UNSUPPORTED_ACCOUNT_TYPE`. Exact credential duplicate comparison remains inside
the child, and repeated success does not append or save twice. Reload failure preserves the saved
account but does not report `READY`.

Cancel, expiry, success, failure, and shutdown close only the matching generation. Stale callbacks
cannot close a newer server. Waiting happens between short RPCs, so it holds no child request lock,
database transaction, worker semaphore, or Telegram handler lock. Shutdown cancels coordinator/UI
tasks, closes discovery, and deletes pending temporary files while preserving successful accounts.

## Local discovery and containers

Pinned librespot binds HTTP to `0.0.0.0`, otherwise chooses a port, and advertises a hostname-
resolved IPv4 address. Its public `HttpRunner.close()` is a no-op. Stage 13.4 therefore uses:

```dotenv
SPOTIFY_CONNECT_HOST_IP=192.168.1.20
SPOTIFY_CONNECT_PORT=24879
```

The host value is non-secret and optional outside pairing. Pairing requires an explicit non-
loopback IPv4 unicast address assigned to a local interface and reachable by the Spotify app on
the same LAN. The child overrides the advertised address only during server construction, uses the
deterministic port, and performs child-private socket/session/Zeroconf teardown.

A normal Docker bridge does not meet this topology: the host LAN address is not assigned inside
the container, multicast discovery does not cross the bridge as required, and the listener is not
the advertised host listener. The child returns `SPOTIFY_PLAYBACK_DISCOVERY_UNAVAILABLE`. A Linux
container needs an operator-reviewed host-network topology in which the configured LAN address is
assigned in its network namespace. A remote VPS cannot pair with an unrelated home LAN through
Zeroconf. No bridge IP is guessed and loopback is rejected.

## Search / Web API credentials

This path uses Spotify OAuth Client Credentials, not Spotify user OAuth:

```text
OWNER / Spotify / Configure Search API
  → bot awaits one private message for the current compound generation
  → message contains exactly two lines: Client ID, then Client Secret
  → bot deletes the Telegram message
  → only after deletion, parent validates structure and wraps two SensitiveValue objects
  → child POSTs https://accounts.spotify.com/api/token with client_credentials
  → child validates token structure without returning or persisting the token
  → child GETs https://api.spotify.com/v1/search with type=track and limit=1 once
  → child updates both pinned OnTheSpot override keys as one logical operation
  → child verifies both saved values and invalidates the pinned OAuth cache
  → Web API readiness becomes observable independently
```

There is no Authorization Code flow, PKCE, callback server, redirect URI, user access token, or
refresh token. The application persists no access token. OnTheSpot owns
`spotify_webapi_override_client_id` and `spotify_webapi_override_client_secret`; SQLite and
application-owned credential stores remain unused.

The route requires a private chat, authoritative configured OWNER identity, current compound
generation, non-forwarded plain text, and a non-replayed message ID. Deletion failure returns
`SPOTIFY_WEBAPI_MESSAGE_DELETE_FAILED` and makes zero child calls. Parsing occurs after deletion
and accepts exactly two bounded, non-empty, control-free lines. Errors never echo fragments.

Both HTTPS requests have bounded connect/read timeouts. Child output contains only allowlisted
status, normalized error, and operational state—never Basic auth, Client Secret, access token, raw
body, search content, or upstream exceptions. Search `429` maps to `RATE_LIMITED`, Spotify reason
`QUOTA_EXCEEDED` remains distinct, and `403` maps to `FORBIDDEN`. These restrictions do not erase
or invalidate credentials after token acquisition succeeds.

Replacement validates pair B before modifying pair A. Failure preserves A. Success sets both
values, performs one save, verifies both, invalidates the pinned token cache, and publishes new
readiness. Save failure restores both prior in-memory values. Invalidation is mandatory because
pinned `spotify_get_oauth_token()` keys reuse by Client ID and expiry but not Client Secret; a
same-ID/new-secret update cannot reuse the old cached token.

## Scope

Stage 13.4 does not implement Telegram search UI, result pagination/ranking, track recognition,
cross-provider search, text-query download, Spotify user OAuth, playback mirroring, or
`/me/player/currently-playing` polling. `MirrorSpotifyPlayback` remains unchanged and disabled.
General disconnect/replacement lifecycle, revocation, crash-atomic file replacement, and broader
hardening remain Stage 13.5. No migration or credential/session table is added, and Stage 13 as a
whole is not complete.
