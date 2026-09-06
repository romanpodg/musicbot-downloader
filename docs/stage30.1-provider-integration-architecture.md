# Stage 30.1 — Provider Integration Architecture and Readiness Contract

Stage 30.1 establishes the application contract for future provider promotion.
It does not activate a new music provider, add credentials, or change download
selection.

## Separate authorities

Five facts that are often confused remain deliberately separate:

1. **Runtime capability** is the static `ProviderCapabilities` table for the
   pinned isolated OnTheSpot implementation. It says what an implementation can
   support in principle.
2. **Application integration** is the immutable `ProviderIntegrationRegistry`.
   It says which features this application intentionally exposes.
3. **Provider health** is the sanitized, observational
   `ProviderHealthEntry`/`ProviderHealthStatus` snapshot for a provider session.
4. **Account readiness** is the managed OWNER-facing
   `ProviderAccountStatus` lifecycle view.
5. **TrackSource readiness** is source-specific: `ProviderResolver` checks the
   persisted source with `MusicProvider.check_source()` and only then emits an
   available candidate.

There is no global “provider ready” boolean. In particular, provider health
`READY` does not make an arbitrary `TrackSource` downloadable, and configured
credentials do not imply health `READY`.

## Application integration manifest

`ProviderIntegrationSpec` contains a provider, explicit search state, stable
search order, account integration mode, stable managed-account presentation
order, and declared authorization methods. It intentionally does not duplicate
media codecs, containers, bitrate, metadata/search/download support, or
authentication capability fields.

The default matrix is:

| Provider | Search | Search order | Account mode | Account order | Authorization |
| --- | --- | ---: | --- | ---: | --- |
| Spotify | ENABLED | 0 | MANAGED | 2 | BROWSER_DEVICE_LINK, COMPOUND_CREDENTIALS |
| Deezer | ENABLED | 1 | MANAGED | 1 | SENSITIVE_SECRET |
| Tidal | ENABLED | 2 | MANAGED | 0 | BROWSER_DEVICE_LINK |
| YouTube Music | DEFERRED | — | NOT_REQUIRED | — | none |
| Bandcamp | DEFERRED | — | NOT_REQUIRED | — | none |
| SoundCloud | DEFERRED | — | NOT_REQUIRED | — | none |
| Apple Music | DEFERRED | — | DEFERRED | — | none |
| Qobuz | DEFERRED | — | DEFERRED | — | none |

The registry requires every `MusicProviderName` exactly once and rejects
missing/duplicate providers, duplicate enabled search orders, and duplicate
managed account orders. Validation against runtime capabilities requires
enabled search support and authentication for managed accounts, but does not
promote a runtime-searchable provider into application search.

Search order and account order are presentation ordering only. Neither is a
quality priority, download-provider preference, candidate rank, or canonical
identity signal.

## Generic search construction

`RuntimeTrackSearchAdapter(runtime, provider)` is the single provider-neutral
Stage 16 adapter. Its parameterized `ProviderTrackMapper` enforces the explicit
provider boundary, rejects malformed or mismatched candidates, bounds runtime
requests, strips empty text, deduplicates provider IDs, and returns only the
provider-neutral `Track` model. The historical Spotify/Deezer/Tidal adapter and
mapper names remain compatibility wrappers.

Composition constructs adapters from
`DEFAULT_PROVIDER_INTEGRATIONS.enabled_search_providers()`, so production search
contains exactly Spotify, Deezer, and Tidal. A child runtime reporting YouTube
Music or Qobuz as searchable cannot add either to application search while its
manifest entry is `DEFERRED`.

The isolated worker's `ONTHESPOT_CAPABILITIES`, `_SEARCHABLE_SERVICES`,
`_DOWNLOAD_SERVICES`, and authenticated-service sets remain low-level runtime
truth. The application manifest is not imported by that worker and does not
remove deferred runtime implementations.

## Accounts and authorization

`MANAGED_PROVIDER_ORDER` is retained only as a compatibility export derived from
the registry. Account management and the runtime account backend obtain their
scope and declared methods from that registry, preserving the UI order Tidal,
Deezer, Spotify and excluding Apple Music, Qobuz, YouTube Music, Bandcamp, and
SoundCloud.

The manifest declares authorization methods; concrete driver construction stays
explicit and typed in composition because drivers are behavior dependencies.
Composition validates that the concrete `(provider, method)` driver keys equal
the manifest declaration exactly. Missing drivers and extra drivers for a
deferred/undeclared provider fail deterministically. No reflection or dynamic
imports are used.

## Search readiness evaluator

`evaluate_provider_search_readiness()` is a pure evaluator over a manifest spec,
static capabilities, a sanitized runtime-searchable fact, and an optional
sanitized health entry. Its precedence is:

1. unsupported static search → `UNSUPPORTED`;
2. deferred application integration → `NOT_INTEGRATED`, even when the child
   reports searchable;
3. enabled integration plus runtime-searchable → `READY`;
4. otherwise normalize `AUTH_REQUIRED`, `UNAVAILABLE`, `UNKNOWN`, and `ERROR`
   health states;
5. health `READY` without runtime-searchable evidence becomes `UNAVAILABLE`;
6. missing runtime evidence becomes `UNKNOWN`, never `READY`.

The evaluator performs no network call and does not force a health sweep before
ordinary search requests. Reasons are short sanitized diagnostic codes, never
raw upstream exceptions or secrets.

## Download boundary and current safety limits

Download readiness remains:

`download_supported` → verified `TrackSource` → `check_source()` →
`ProviderResolver` → `QualityResolver`.

Provider health `READY` alone cannot produce an available download candidate.
The Qobuz `SESSION_UNVERIFIED` health boundary remains non-ready, and Apple
Music's subscription/premium requirement remains enforced by existing account
health normalization. Stage 30.1 adds no provider authorization flow.

## Promotion checklist

Future provider promotion must proceed in order:

1. verify pinned runtime capability;
2. verify adapter and metadata normalization;
3. if authentication is required, implement and verify managed authorization;
4. verify sanitized runtime readiness;
5. change the application integration state;
6. run provider-specific search, canonical, and download regressions.

## Non-goals

Stage 30.1 does not enable YouTube Music, Qobuz, Apple Music, Bandcamp, or
SoundCloud; add authorization flows or settings; add provider preferences or
ranking; alter recognition, canonical identity, quality, fallback, credentials,
queues, persistence, migrations, dependencies, or Stage 30.2 functionality.
