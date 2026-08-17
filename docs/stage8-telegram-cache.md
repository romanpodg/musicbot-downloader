# Stage 8 Telegram cache and delivery foundation

Stage 8 adds completed-result reuse without implementing the user-facing Telegram bot.

## Operator setup

1. Create the downloader bot with BotFather, or use the bot intended for future Stage 9 delivery.
2. Create a private cache channel, private supergroup, or another private chat.
3. Add the bot and grant permission to send files/messages.
4. Configure the same bot token and the destination chat ID:

   ```env
   BOT_TOKEN=
   TELEGRAM_CACHE_CHAT_ID=
   ```

   `TELEGRAM_CACHE_CHAT_ID` accepts the numeric Bot API chat ID (including negative channel or
   supergroup IDs) or another Bot API-supported chat identifier such as a username. Do not commit
   either value. The bot token is a secret; the cache chat ID is operational configuration.

5. Apply migrations and verify that Telegram recognizes the configured bot:

   ```bash
   uv run alembic upgrade head
   uv run python -m app.tools.telegram_cache verify-gateway
   ```

`verify-gateway` performs `getMe` and reports only the numeric bot ID, username, configured cache
destination, and status. It does not start polling and does not upload a file. Posting permission is
ultimately verified by an actual queue upload or the opt-in external integration test.

## Runtime boundaries

`compose_stage8(database, settings)` resolves the immutable process-local bot identity once and
constructs:

- `TelegramFileCacheService`, the SQLite metadata/reference service;
- `AiogramTelegramGateway`, the library-specific Telegram API adapter;
- `TelegramCacheUploadExecutor`, the generic Stage 7 upload implementation;
- `DeliveryPreparationService`, the cache-first Stage 9-facing application service.

The factory starts no Dispatcher, update loop, webhook, bot handlers, or background validation.
Queue owners may pass the returned upload executor to `UploadWorkerBackend` and the same wake event
and subscriber notifier into the factory. Future Stage 9 application startup owns that lifecycle.

## Cache and upload lifecycle

The unique key is `(telegram_bot_id, track_id, quality_profile)`. One mutable row is retained per
logical key. ACTIVE is reusable; INVALID is ignored. A replacement upload updates and reactivates
the row while preserving its original `created_at` and updating `updated_at`.

The download-to-cache sequence is:

```text
validated Stage 6 artifact
  -> UploadJob with durable actual media/provenance facts
  -> exact ACTIVE cache check
  -> Telegram AUDIO (MP3/M4A) or DOCUMENT (FLAC/other)
  -> normalized file_id/file_unique_id/chat/message receipt
  -> cache transaction commits
  -> UploadJob SUCCEEDED
  -> WAITING subscribers READY and flight closes
  -> local artifact released
```

If a worker retries after cache persistence but before UploadJob success, the executor finds the
ACTIVE row and returns success without another Telegram call. The unavoidable residual window is a
process crash after Telegram accepts the upload but before SQLite persists its receipt; a retry may
leave an extra cache-chat message, though the database still converges to one authoritative row.

Telegram rate limits, network failures, and server failures become typed retryable upload errors.
Structured `retry_after` becomes the persisted Stage 7 `available_at` delay. Invalid authorization,
permission/configuration failures, bad requests, and invalid receipts are terminal. The adapter does
not log Telegram exception strings, and the application logging filter redacts configured secrets
and token-bearing `api.telegram.org/bot<TOKEN>/...` URLs.

## Inspection and manual verification

Inspect counts and bounded rows:

```bash
uv run python -m app.tools.telegram_cache status
uv run python -m app.tools.telegram_cache status --bot-id <BOT_ID>
uv run python -m app.tools.telegram_cache list --limit 50
uv run python -m app.tools.telegram_cache list --status ACTIVE --track-id <TRACK_ID>
```

Inspect one exact cache key. Supplying `--bot-id` keeps the command metadata-only; omitting it uses
`getMe` to resolve the current bot:

```bash
uv run python -m app.tools.telegram_cache get <TRACK_ID> MP3_320 --bot-id <BOT_ID>
uv run python -m app.tools.telegram_cache get <TRACK_ID> MP3_320
```

Exercise cache-first admission (`CACHE_HIT` or `PENDING`):

```bash
uv run python -m app.tools.delivery prepare <TRACK_ID> MP3_320
uv run python -m app.tools.delivery prepare <TRACK_ID> MP3_320 --request-key manual-check
```

Invalidate a row without starting a replacement download:

```bash
uv run python -m app.tools.telegram_cache invalidate <CACHE_ID> \
  --reason-code MANUAL_INVALIDATION
```

Full `file_id` output is hidden by default. Add `--show-file-id` to cache `get` or `list` only when
direct internal diagnostics require it. `file_unique_id` is diagnostic and cannot be used to send
the file.

## Opt-in external verification

Set a dedicated test chat explicitly, then run:

```bash
BOT_TOKEN="..." TELEGRAM_TEST_CACHE_CHAT_ID="..." uv run pytest -m external \
  tests/integration/test_telegram_external.py
```

The test creates a tiny silent WAV locally and uploads it as a document. It never contacts a music
provider and commits no copyrighted fixture. It intentionally does not delete arbitrary Telegram
messages, so the small synthetic message remains in the configured test chat.

## Deliberate limitations

- No cache eviction, refresh scheduler, negative provider cache, or periodic Telegram validation.
- No download from Telegram for conversion; each QualityProfile has an exact independent row.
- No Telegram user handlers or subscriber fanout. Stage 9 will reuse ACTIVE `file_id` values.
- SQLite and Telegram cannot provide exactly-once network/DB atomicity.
