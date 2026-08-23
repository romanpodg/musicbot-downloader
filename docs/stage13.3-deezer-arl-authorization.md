# Stage 13.3 — Deezer ARL Authorization

Stage 13.3 adds the Deezer sensitive-secret driver to the accepted Stage 13.1 provider-account
architecture. It does not create a second account-management subsystem and does not add provider
authentication methods to `MusicProvider`.

## Lifecycle and authority

```text
authoritative OWNER in a private chat
  → Provider Accounts / Deezer / Connect Deezer
  → process-local generation waits for sensitive text
  → OWNER submits an ARL from their own authenticated Deezer session
  → bot deletes that incoming Telegram message immediately
  → isolated child validates the ARL through the HTTPS Deezer gateway
  → child persists the validated account in OnTheSpot configuration
  → existing runtime reload executes with the secure Deezer login adapter
  → runtime Provider Health recognizes an authenticated Deezer account
  → normalized status becomes READY
```

Only the configured authoritative `OWNER` may start, submit to, or cancel this flow. The secret
handler requires a private, non-forwarded, plain-text message and a current Deezer generation that
is specifically waiting for its first sensitive input. ADMIN and USER roles, owner-role rows whose
Telegram identity does not match `OWNER_ID`, group/channel messages, forwarded messages, captions,
documents, photos, stale generations, and replayed Telegram message IDs are not accepted.

There is at most one active authorization flow per provider. Waiting for text occupies no
OnTheSpot request lock, worker semaphore, database transaction, or long-lived Telegram handler.
Cancellation before submission is local and makes no provider request. Once child submission has
started, cancellation does not interrupt validation or `config.save()`; the in-flight result is
allowed to reach a deterministic terminal state. Pending secret-entry state is process-local and
is intentionally not durable across restart. Shutdown cancels untouched waiting flows and waits
for an already-submitted operation before closing the child.

## Telegram deletion gate and transient parent handling

An ARL is a sensitive credential. Telegram necessarily delivers its message text to the
application process, so the ARL transiently reaches that process; Stage 13.3 does not claim
otherwise. After authority, private-chat, current-generation, and waiting-state checks, the bot
deletes the incoming message before constructing the `SensitiveValue` handoff or invoking the
provider backend. `SensitiveValue.__str__` and `SensitiveValue.__repr__` are redacted and the object
cannot be serialized by the ordinary JSON encoder.

If Telegram deletion fails, authorization fails closed with a sanitized error. The child receives
zero authorization calls, the ARL is neither validated nor persisted, and the pending generation
is terminated. The bot never echoes, quotes, replies with, masks, truncates, fingerprints, or
otherwise displays the ARL. Application logging does not serialize Telegram `Update` or `Message`
objects, and the secret-receipt path catches failures with sanitized codes rather than raw message
or exception diagnostics.

## Isolated validation and persistence

The dedicated `deezer_arl_authorize` JSON-lines RPC carries the ARL only in its request. Its result
is a strict allowlist: either a persisted status or a normalized error code. It cannot return the
ARL, cookies, request session, `checkForm`, license token, raw Deezer response, profile payload, or
raw OnTheSpot account dictionary.

The child first rejects empty, whitespace-only, multiline, control-character, excessive, and
implausible cookie values. It then creates a dedicated requests session with bounded connect/read
timeouts and calls only:

```text
https://www.deezer.com/ajax/gw-light.php
method=deezer.getUserData
```

There is no plaintext HTTP fallback or downgrade. HTTP status, JSON structure, authenticated
nonzero user identity, `checkForm`, and required runtime option fields are checked; HTTP 200 alone
is insufficient. Invalid/anonymous credentials, timeout, network failure, malformed response,
upstream failure, and persistence failure receive distinct sanitized codes where deterministic.
No response body, cookie header, credential, or raw requests exception is logged.

Only a successfully validated ARL is appended using OnTheSpot v1.8.1's owned account structure.
Unrelated accounts are preserved. Exact-credential duplicate detection occurs only inside the
child and avoids repeated appends without exporting a fingerprint. A save failure restores the
child's prior in-memory account list. SQLite, environment files, application JSON, Telegram state,
callbacks, audit payloads, and metrics do not store the ARL; OnTheSpot configuration remains the
only durable credential owner.

## Secure runtime reload and status sanitization

Pinned OnTheSpot v1.8.1 normally posts Deezer login to plaintext HTTP and sets runtime
`username = arl`. Stage 13.3 installs a minimal child-only Deezer runtime login adapter before both
initial account loading and runtime reload. The adapter repeats the authenticated login through
the HTTPS gateway with bounded timeouts, produces the runtime token/session shape expected by the
downloader, and sets the upstream runtime username field to an empty safe value. It never calls or
falls back to the pinned plaintext login function.

Application-visible provider status contains no account-label field. Consequently, the upstream
Deezer username cannot become a Telegram label, and the ARL is never represented by a prefix,
suffix, hash, truncation, or mask. The UI renders simply `Status: Ready` when runtime health is
ready.

READY is reported only after all four facts hold: server-side validation succeeded, OnTheSpot
configuration was saved (or already contained the same active credential), secure runtime reload
completed, and runtime Provider Health returned Deezer READY. If reload or verification fails, a
sanitized reload error is returned and the persisted account remains available for a later Refresh
or restart.

## Scope

Tidal continues to use the unchanged Stage 13.2 browser/device flow. Spotify credentials remain
Stage 13.4. General replacement, revocation, account cleanup, and crash/lifecycle hardening remain
Stage 13.5. No database migration or credential/session table is introduced, and Stage 13 as a
whole is not complete.
