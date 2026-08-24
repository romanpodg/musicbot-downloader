# Stage 16 — Provider Search Integration

Stage 16 connects the completed Stage 15 provider-neutral search boundary to the existing isolated
OnTheSpot provider runtime for Spotify, Deezer, and Tidal. It changes neither provider account
authorization, credential persistence, lifecycle reconciliation, download planning, nor Telegram
callback namespaces.

## Adapter architecture

```text
Telegram UX
  -> SearchTracksUseCase
  -> TrackSearchService
  -> TrackSearchProviderRegistry
  -> SpotifySearchAdapter | DeezerSearchAdapter | TidalSearchAdapter
  -> MusicProvider.search_tracks
  -> isolated OnTheSpot child runtime
```

`app.composition.compose_stage9` is the sole production assembly point. It registers the three
adapters in the Stage 15 `TrackSearchProviderRegistry` against the existing `MusicProvider`
instance. The search service remains provider-neutral: it invokes registered adapters
sequentially, preserves provider order, and neither ranks nor merges catalog entries across
providers.

The adapters in `app.providers.search_adapters` are intentionally thin. Each builds the existing
runtime `TrackSearchRequest` for exactly its declared provider, calls the existing
`MusicProvider.search_tracks` boundary, and passes only the returned sanitized candidates to its
mapper. The runtime keeps authentication, active account/session selection, and child-process IPC
ownership. No adapter receives a credential, account backend, database, Telegram object, download
service, or raw upstream provider object.

The runtime currently bounds one provider query to ten lightweight catalog candidates. Adapters
apply that runtime bound while the Stage 15 service still enforces the caller's aggregate limit
across the sequential provider set.

## Mapping and normalization

`app.providers.search_mappers` contains a dedicated `SpotifyTrackMapper`, `DeezerTrackMapper`, and
`TidalTrackMapper`. Each accepts only the legacy sanitized `TrackSearchCandidate` shape and emits
the immutable Stage 15 `Track` model. The resulting domain value carries a normalized title,
display artist, provider identity, and provider track ID; its generic search-result ID is namespaced
as `search:<provider>:<track-id>`. It never embeds a Spotify, Deezer, Tidal, or OnTheSpot object.

The current runtime's lightweight search response does not safely supply album or duration, so the
existing optional `Track.album` and `Track.duration_ms` fields are `None`. Stage 16 deliberately
does not perform follow-up metadata calls: search adapters remain request/call/map components, and
metadata enrichment stays outside their scope.

Candidates without a usable title, artist, or provider track ID are ignored rather than inventing
metadata. Candidate URLs never enter the Stage 15 domain model.

## Availability and compatibility

The existing runtime maps unavailable or unauthenticated provider execution to typed
`ProviderUnavailable` or `ProviderAuthenticationError`. Adapters preserve those typed failures.
`TrackSearchService` logs only a normalized provider identity, continues to the next configured
provider, and raises `TrackSearchUnavailable` only when none completes. The Stage 14 UX error
boundary localizes that outcome without exposing API errors, account details, or authentication
state.

There are no database migrations, settings, new credentials, account records, or provider lifecycle
changes. The pre-existing Stage 3 source-discovery use of `MusicProvider.search_tracks` remains
separate from the Stage 15 catalog-result domain: its candidates are converted only by these Stage
16 mappers for user search.
