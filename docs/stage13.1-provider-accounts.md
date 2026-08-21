# Stage 13.1 — Provider Account Management Foundation

Stage 13.1 introduces the provider-neutral architecture and OWNER-only Telegram UI needed by later
provider authorization stages. It does not implement a provider login flow.

## Architecture and authority

`PROVIDER_ACCOUNTS_MANAGE` is granted only when the persisted user role is `OWNER` and the row's
Telegram ID exactly matches configured `OWNER_ID`. Existing administrators retain their Stage 10
permissions but do not receive this capability. Telegram establishes this authority before parsing
an `adm5` callback or running an account operation, and account management remains private-chat
only.

Provider account state is represented by sanitized, provider-neutral DTOs with `READY`,
`NOT_CONFIGURED`, `AUTH_REQUIRED`, `AUTHORIZING`, `ERROR`, and `UNSUPPORTED` states. The account
backend is a separate contract from `MusicProvider`. Its Stage 13.1 implementation normalizes the
existing isolated child-process provider-health/reload boundary for Tidal, Deezer, and Spotify.
It never returns account identities, upstream configuration objects, or credentials.

`ProviderAuthorizationCoordinator` owns only ephemeral in-process flow coordination and permits at
most one active flow per provider. It defines typed unsupported, already-active, cancelled, ready,
and failed outcomes plus driver boundaries for browser/device-link, sensitive-secret, and compound
playback/API credential authorization. No authorization driver is registered in Stage 13.1, so the
Telegram UI intentionally renders no Connect button.

## Explicit non-goals

- No Tidal device authorization is implemented yet.
- No Deezer ARL authorization or secret submission is implemented yet.
- No Spotify playback/search credential setup is implemented yet.
- No provider credential, pending authorization session, or duplicated READY flag is stored in the
  application SQLite database.
- No account-management schema change or Alembic migration is introduced.
- No Stage 13.2, 13.3, 13.4, or 13.5 behavior is included.

OnTheSpot remains the authentication source of truth inside its existing child process. The main
application process still imports neither OnTheSpot nor librespot.
