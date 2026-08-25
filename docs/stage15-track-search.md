# Stage 15 — Track Search Architecture

Stage 15 adds the provider-neutral search foundation only. It does not add a Spotify, Deezer,
Tidal, YouTube, or other provider API integration; it does not persist results, recognize tracks,
match recordings, or start download work.

## Architecture

```text
Telegram /search or Search menu
  -> app.telegram.ux_handlers
  -> UxFlowService
  -> SearchTracksUseCase
  -> TrackSearchService
  -> TrackSearchProviderRegistry
  -> SpotifySearchAdapter | DeezerSearchAdapter | TidalSearchAdapter (Stage 16)
```

The Telegram adapter only collects a private-chat query and renders localized message keys. It has
no provider API, database, download, or formatting decisions. The application flow creates a
provider-neutral `TrackSearchRequest`, invokes the use case, and uses the Stage 14 error boundary
for failures.

## Domain and contracts

`app.core.search` defines immutable, provider-independent `Artist`, `Album`, `Track`,
`TrackSearchRequest`, and `TrackSearchResult` models. A search `Track` is a normalized catalog
result and is deliberately distinct from both the persisted canonical `storage.models.Track` and
the earlier Stage 3 `TrackSearchRequest` used solely for verified source discovery. Stage 17 adds
an in-memory recognition layer after this result boundary; strict persisted-recording matching and
all source/download selection remain separate concerns.

`app.providers.search.TrackSearchProvider` is the only future adapter contract: an adapter declares
one `MusicProviderName` and returns normalized `Track` values for a request. It receives no
Telegram, database, queue, download, or provider-account lifecycle objects.

`TrackSearchProviderRegistry` is composition-owned and maps provider names to contracts. Stage 16
registers Spotify, Deezer, and Tidal search adapters at the composition root without modifying the
service, use case, or Telegram handler. The runtime and mapper details are documented in
[`stage16-provider-search-integration.md`](stage16-provider-search-integration.md).

`TrackSearchService` selects requested registered providers (or all registered providers), bounds
the aggregate result set, drops adapter results whose declared provider is inconsistent, and
deduplicates only identical `(provider, provider_track_id)` pairs. It does not merge tracks across
providers. If no requested provider completes safely, it raises `TrackSearchUnavailable`; that is
localized as “Search is temporarily unavailable.” without raw exception details.

## UX state and scope

Stage 14's in-memory state foundation now supports `SEARCH_INPUT`, `SEARCHING`, and
`SEARCH_RESULTS` alongside `IDLE`. `/search` and the Search menu request a query; a submitted query
creates `TrackSearchRequest`. Production composition searches the configured Spotify, Deezer, and
Tidal runtime catalogs through the Stage 16 adapters. Stage 17 calls recognition after the search
result boundary but does not add Telegram selection or download actions. Tests cover both injected
contract providers and the real adapter layer against a mock provider runtime. Search result
selection and all download states remain outside this stage.

## Compatibility

This stage creates no database tables or migrations, no provider credentials, no callbacks outside
the existing `ux1` namespace, and no changes to admin/provider-account authorization or queue
ownership. The existing `MusicProvider.search_tracks` path continues to belong to Stage 3 verified
source discovery and is not used by the new search architecture.
