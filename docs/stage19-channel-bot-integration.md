# Stage 19 — Channel/Bot Integration

Stage 19 extends the completed Stage 14–18 Telegram search-to-delivery flow from a private-user
assumption to an explicitly routed delivery destination. It adds no downloader, queue, provider
adapter, worker pool, or replacement role system.

## Context and delivery model

Telegram transport objects are converted once at the router boundary into the transport-neutral
`TelegramContext(user_id, chat_id, chat_type)`. The supported types are `private`, `group`,
`supergroup`, and `channel`.

`DeliveryTarget(chat_id, target_type)` is independent of the requesting user. Its concrete values
are `PrivateUserTarget`, `GroupChatTarget`, and `ChannelTarget`. The initiating user remains the
owner of the confirmation and durable request; it is not assumed to be the final recipient.

```text
confirmed Stage 17 Track
  -> Stage 18 DownloadService (actor-owned confirmation)
  -> DeliveryTargetResolver
  -> existing TelegramDeliveryRequest outbox
  -> existing SingleFlight / download queue / upload queue / cache
  -> existing TelegramDeliveryWorker
  -> DeliveryTarget
```

The outbox retains `telegram_chat_id` as the immutable request-origin and callback-ownership key.
Stage 19 adds `delivery_chat_id` and `delivery_target_type`; only those delivery fields are used by
the cached-file fanout worker. Legacy rows without the new values retain their historical private
chat delivery fallback, while every Stage 19 admission writes an explicit target. This preserves old
request idempotence and makes delivery routing replaceable without changing queue or download
behavior.

## Chat policy and channel binding

Global `USER`, `ADMIN`, and `OWNER` roles are unchanged. `ChatPolicy` is a separate chat-level
allow/route decision:

```text
ChatPolicy(chat_id, allow_downloads, delivery_mode)
```

Private interactions route to `PrivateUserTarget`. Groups and supergroups require an explicit,
enabled policy. `USER` mode routes the completed file to the requesting user's private chat;
`CHAT` mode routes it to the group. The router freshly evaluates the persisted user ban state,
chat policy, and bot send capability for every opening, search input, and callback.

Channels additionally require a durable `ChannelBinding(channel_id, status)`. Only a matching
`CONNECTED` binding may produce `ChannelTarget`; `NO_PERMISSION` and `DISCONNECTED` fail closed.
Binding management UI is deliberately outside Stage 19—the model/repository is the explicit
configuration seam, not a new administration system.

`ChatRateLimitPolicy` is an unused extension point only. Stage 19 introduces no throttling,
distributed state, or queue changes.

## Callback and permission security

Stage 18 `dl18` callbacks remain short opaque tokens. They contain no provider IDs, credentials,
paths, or job internals. Confirmation state now records the complete `TelegramContext` and a
15-minute expiry. Selection, cancellation, and confirmation require the same user, chat ID, and
chat type; cross-chat, cross-user, stale, and expired callbacks are rejected.

The existing first-quality callback rechecks the current chat policy/bot capability and atomically
checks the durable request's origin chat along with its user ownership before it can transition.
Global administration authorization remains centralized in `TelegramAuthorizationService` and
continues to be private-chat-only.

## Workflow and boundaries

An enabled group user follows the unchanged Stage 15 search, Stage 17 recognition, and Stage 18
confirmation workflow. Only the captured context and resolver result differ. A configured channel
has the same durable delivery foundation and can receive a `ChannelTarget` result. Telegram channel
posts have no individual actor, so Stage 19 does not invent an anonymous command workflow; a
user-owned request is always required for confirmation security.

`DeliveryService` remains cache-upload-only and knows neither chat types nor policies. The queue
and download pipeline receive only canonical track and quality data. Removing channel routing would
therefore affect the resolver, policy/binding tables, and Telegram adapters—not download logic.

## Validation

Deterministic tests cover context and target validation, resolver behavior, policy/binding storage,
fresh user/policy/bot-permission checks, callback user/chat/expiry validation, and private, group,
and configured-channel cached-file delivery with no real Telegram API.
