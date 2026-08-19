# Stage 11 Internal API and Deep-Link Registry

## Boundary and lifecycle

The FastAPI application is an HTTP transport over `DeepLinkRegistryService`; it is not a second
download pipeline. When enabled, an embedded asynchronous uvicorn server runs in the same process
and lifecycle as aiogram, queue/delivery/album workers, the shared SQLite engine, and the one
serialized OnTheSpot child. Access logging is disabled because token-bearing lookup/revoke paths
do not need to be recorded. Swagger, Redoc, OpenAPI routes, and CORS are not enabled.

The listener defaults to disabled and `127.0.0.1:8081`. It is intended only for localhost or a
trusted private container/network path. Public exposure without private transport or a hardened
reverse proxy is unsupported. `python -m app.main --check` validates configuration and builds the
ASGI route graph without resolving URLs, contacting Telegram/providers, or binding a socket.

## Authentication

All registry endpoints accept only `Authorization: Bearer <INTERNAL_API_TOKEN>`. The configured
secret must be trimmed and at least 32 characters when the API is enabled; it is held in a
`SecretStr`, compared with `secrets.compare_digest`, never persisted, and never returned or logged.
Missing, malformed, and incorrect credentials all return the same `401 UNAUTHORIZED` response.
The public `d1_...` token cannot authenticate the API. Telegram ADMIN/OWNER roles are unrelated.

The unauthenticated health endpoint returns generic liveness only:

```http
GET /internal/v1/health

200 {"status":"ok"}
```

## Endpoints

### Register

```http
POST /internal/v1/deep-links
Authorization: Bearer <secret>
Idempotency-Key: publishing-item-123
Content-Type: application/json

{"url":"https://open.spotify.com/track/..."}
```

The idempotency header is optional but recommended and has a 128-character maximum. Success is:

```json
{
  "target_type": "track",
  "status": "active",
  "start_parameter": "d1_<32 base64url characters>",
  "deep_link_url": "https://t.me/current_bot_username?start=d1_...",
  "created": true,
  "created_at": "2026-08-20T00:00:00Z",
  "revoked_at": null
}
```

Supported input types are Track and Album. URL classification uses the existing allow-listed
provider parser, which rejects unknown hosts, credentials in URLs, fragments, non-HTTP schemes,
unexpected ports, playlists, artists, and arbitrary local/private targets. Requests and fields are
bounded; errors and logs do not echo raw URLs.

Track registration runs existing Stage 3 resolution with verified discovery and stores the
canonical Track foreign key. Album registration performs safe provider URL classification and
stores only the provider `.value` plus provider Album ID. It deliberately creates no permanent
canonical Album snapshot and no per-user `telegram_album_requests`/items.

### Inspect

```http
GET /internal/v1/deep-links/{start_parameter}
Authorization: Bearer <secret>
```

The response contains the normalized target type/status, start parameter, timestamps, and either
the canonical `track_id` or provider Album identity. It contains no ORM payload, provider account
data, credentials, database path, filesystem path, worker identity, or source URL.

### Revoke

```http
POST /internal/v1/deep-links/{start_parameter}/revoke
Authorization: Bearer <secret>
```

Revocation changes `ACTIVE` to `REVOKED` and sets `revoked_at`. Repeating it is idempotent. Rows are
not deleted and links do not expire or become consumed. A concurrent click that materializes its
Telegram request before revocation commits may continue; lookups after commit are unavailable.
Already-created Track/Album cards and downstream delivery state are unaffected.

## Stable errors

Errors use one machine-readable envelope:

```json
{"error":{"code":"IDEMPOTENCY_KEY_CONFLICT","message":"..."}}
```

The mappings are:

- `400/413 INVALID_REQUEST`: malformed/oversized JSON, fields, or idempotency key;
- `401 UNAUTHORIZED`: any Bearer authentication failure;
- `404 DEEP_LINK_NOT_FOUND`: unknown or malformed token on authenticated endpoints;
- `409 IDEMPOTENCY_KEY_CONFLICT`: same bot/key used for another canonical request;
- `422 INVALID_MEDIA_URL`: invalid or unsupported provider URL;
- `422 UNSUPPORTED_MEDIA_TYPE`: recognized out-of-scope media such as a playlist;
- `422 ALBUM_TARGET_INVALID`: invalid bounded Album target;
- `503 TRACK_RESOLUTION_FAILED`: normalized provider metadata/authentication failure;
- `500 INTERNAL_ERROR`: sanitized unexpected failure.

No response includes raw provider exceptions or stack traces.

## Registry and token design

Alembic revision `20260820_0010` creates `deep_link_registry`. CHECK constraints enforce stable
`TRACK`/`ALBUM` and `ACTIVE`/`REVOKED` values, a positive bot ID, exactly one target shape, bounded
provider IDs/keys, 64-character SHA-256 fingerprints, strict 35-character `d1_` tokens, and
consistent revocation timestamps. Track deletion is restricted. Unique constraints on
`(telegram_bot_id, token)` and `(telegram_bot_id, idempotency_key)` are the final concurrency
invariants; nullable keys still allow independent links.

Each token is `d1_ + secrets.token_urlsafe(24)`: 24 random bytes (192 bits), unpadded base64url,
and safely below Telegram's 64-character start-parameter limit. A bounded retry handles the
practically impossible database collision. Tokens encode no IDs, provider, user, quality, expiry,
or publication identity.

The idempotency fingerprint is SHA-256 over the NUL-separated stable values `target_type`,
`provider.value`, and provider item ID after safe URL classification. Tracking differences that
canonicalize to one provider identity replay without persisting the request URL. Failed resolution
creates no registry/idempotency row, so retry is safe.

## Telegram behavior

Only strict `d1_[A-Za-z0-9_-]{32}` `/start` payloads reach the registry. Unknown, malformed, and
revoked Stage 11 tokens share the localized “download link unavailable” response in English and
Russian. Unrelated `/start` payloads keep normal welcome behavior.

An ACTIVE Track target calls direct `request_track_id` admission, preserving message-id replay
idempotency and avoiding provider URL re-resolution. An ACTIVE Album target calls
`request_album_target(provider, provider_album_id)`, which resolves the provider release and then
uses the existing durable Album snapshot/card service. Normal first-quality and explicit action
semantics remain intact. Opening alone creates no DownloadJob, UploadJob, cache operation,
subscriber, artifact, or provider media work.

## Configuration

```env
INTERNAL_API_ENABLED=false
INTERNAL_API_HOST=127.0.0.1
INTERNAL_API_PORT=8081
INTERNAL_API_TOKEN=
```

When disabled, no secret is required and no listener starts. When enabled, host, port `1..65535`,
and a strong secret are mandatory. Changing the API secret and restarting affects HTTP
authentication only; registry tokens remain valid. Changing `BOT_TOKEN` for the same Telegram bot
also preserves links because scope uses `getMe.id`. A different bot ID cannot resolve them. New
responses use the current `getMe.username`; old published URLs depend on Telegram's username
behavior if that username later changes.
