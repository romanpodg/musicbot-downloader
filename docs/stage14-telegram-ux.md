# Stage 14 — Telegram Bot UX Foundation

Stage 14 adds a scalable user-experience boundary without changing the completed Stage 9 delivery
pipeline or the Stage 10–13.5 administration and provider-account workflows.

## Architecture

```text
Telegram update
  -> app.telegram.ux_handlers (transport adapter)
  -> app.application.ux.flows (navigation orchestration)
  -> existing application services
  -> domain and infrastructure
```

The Stage 14 handler receives a Telegram command/callback, creates the existing sanitized user
profile, asks the UX flow for a screen, and renders the returned message key and keyboard. It does
not contain menu decisions, persistence rules, provider logic, or user-facing string literals.

The existing `app.telegram.handlers` retains Stage 9's established URL, Track Card, Album Card,
quality, and deep-link routes. Moving those completed workflows is outside Stage 14. Deep-link
`/start` payloads continue to use that router; normal `/start`, `/help`, and the new `/menu` use the
Stage 14 router. The ADMIN/OWNER router is registered separately and retains its strict `adm*`
callback contracts, including `adm5` provider-account authorization callbacks.

## New modules

- `app/application/ux/flows/navigation.py`: transport-neutral welcome/help/menu flow and screen
  result.
- `app/application/ux/services/state.py`: `IDLE`, `MENU`, `PROCESSING`, and `ERROR` state
  transitions, plus reserved future states.
- `app/application/ux/services/progress.py`: a minimal `progress.update(user_id=..., state=...)`
  contract for later long-running operations.
- `app/telegram/messages.py`: stable UX message names over the existing JSON localization service.
- `app/telegram/keyboards.py`: one factory for main-menu, navigation, confirmation, and cancel
  keyboards.
- `app/telegram/callbacks.py`: versioned callback encoder/parser.
- `app/telegram/ux_handlers.py`: thin aiogram router registered by the normal composition root.

## Messages and menus

All Stage 14 user text comes from `app/i18n/locales/*/messages.json` via `UxMessageService`. Its
named categories cover welcome, help, account, provider, error, and system text and can be extended
for localization without changing handlers.

`/start` displays the welcome and main menu. `/help` displays usage guidance. `/menu` returns the
main menu. The menu includes Search, Account, Providers, and Settings. Search remains explicitly a
future capability; the Providers screen exposes no account state, credentials, or authorization
action. Existing `/quality` and `/language` remain the user preference interfaces.

## Callbacks and state

Stage 14 callbacks use `ux1:<action>:<entity>[:<identifier>]`, for example:

```text
ux1:menu:open
ux1:menu:section:settings
ux1:operation:cancel:current
```

The parser validates the version, part syntax, optional identifier, and Telegram's 64-byte callback
limit. The router additionally accepts only the currently implemented `menu` routes. Callback data
contains navigation identifiers only, never user data, provider credentials, tokens, paths, or error
details. The versioned namespace leaves independent room for Stage 15 search and Stage 18 download
callbacks.

The current navigation state is in memory: `IDLE`, `MENU`, `PROCESSING`, and `ERROR`. Reserved
states are `SEARCHING`, `SELECTING_TRACK`, `DOWNLOADING`, and `UPLOADING`. This state intentionally
does not replace Stage 9's durable SQLite Track/Album Card and delivery state; those workflows must
survive restarts and retain their existing owners.

## Error boundary and compatibility

`UxErrorService` maps validation, database, provider, and unexpected failures to localized generic
messages. It does not render exception values, stack traces, SQL text, provider diagnostics, or
credential material. The provider-account boundary remains OWNER-only/private-chat enforced, and
its sensitive input deletion rule is unchanged.

Stage 14 does not implement track search, search selection, downloads, actual progress updates,
provider-account management, or any Stage 15+ behavior.
