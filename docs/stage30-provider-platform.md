# Stage 30 provider platform — current-state contract

This is the authoritative current-state reference for the Stage 30 provider
platform. Historical Stage 30.1–30.5 documents record the decisions made at
their respective stages; they do not override this document.

## Provider set and account model

The enabled search order is **Spotify, Deezer, Tidal, YouTube Music, Qobuz,
Apple Music**. The managed-account presentation order is **Tidal, Deezer,
Spotify, Qobuz, Apple Music**. YouTube Music is the one public,
application-unmanaged provider. Bandcamp and SoundCloud remain deferred; they
are not enabled search providers.

| Provider | Search | Account model | Current native-media truth | Exact current profile behavior |
| --- | --- | --- | --- | --- |
| Spotify | enabled | managed browser/device link or compound credentials | Vorbis/Ogg lossy | no safe plan for the five exact application profiles |
| Deezer | enabled | managed sensitive ARL | MP3/FLAC, selected during preflight/media acquisition | exact media evidence is authoritative |
| Tidal | enabled | managed browser/device link | AAC/M4A or FLAC, selected during preflight/media acquisition | exact media evidence is authoritative |
| YouTube Music | enabled | public, no managed application account | AAC/M4A nominal 128 kbps | `AAC_128` direct only |
| Qobuz | enabled | managed child-owned email/password session | genuine FLAC/lossless | `LOSSLESS` direct; safe transcodes to every current lossy profile |
| Apple Music | enabled | managed child-owned media-user-token session | AAC/M4A nominal 256 kbps | `AAC_256` direct only |

The application exposes five and only five profiles: `AAC_128`, `AAC_256`,
`MP3_128`, `MP3_320`, and `LOSSLESS`. Lossless-to-lossy transcode is allowed;
lossy-to-lossy transcode, fake lossless, and bitrate upscale are forbidden.
The actual file is always re-probed. Deezer and Tidal capability declarations
can create only preflight plans until that media evidence exists.

## Separate readiness authorities

Runtime capability, application integration, provider runtime health, managed
account readiness, and `TrackSource` availability are separate facts. A
provider `READY` state does not prove that every track can be acquired.
`MusicProvider.check_source()` remains the authoritative source-specific
availability check. Credentials, sessions, manifests, and upstream objects stay
inside the isolated OnTheSpot child; the parent keeps only sanitized lifecycle
and health state.

Managed-account reset is a local application/runtime reset. It removes the
selected provider's child-owned local authentication state and reloads status;
it is not a claimed remote-provider revocation. Apple Music and Qobuz use the
same managed lifecycle and reset path as Tidal, Deezer, and Spotify.

## Resolution, recognition, and durable identity

Quality planning happens before recognition/source affinity. The exact ordering
is `plan_sort_key()`: direct confirmed, direct preflight, transcode confirmed,
then transcode preflight, followed by stable neutral provider/source identities.
Stage 25 still exhausts safe same-provider account fallback before lower-ranked
cross-provider fallback.

Recognition keeps provider-neutral preliminary scoring, conservatively selects
recording-diversified enrichment finalists, makes at most five metadata calls,
then applies the final Stage 29 clustering and ambiguity-aware decision.
Equivalent provider permutations must not change the semantic recording result,
although their representative source may differ.

Admission identity and acquisition provenance intentionally differ. Direct
admission can retain its source provider in Stage 24 `DownloadRequest` and
`TelegramCacheKey`; successful `DownloadResult`, `ProviderAttempt`, `UploadJob`,
and Stage 8 `TelegramFileCache` retain the provider that actually acquired the
media. Thus an Apple direct admission that safely falls back to Qobuz can
correctly retain Apple as admission identity and Qobuz as `source_provider`.

Stage 30.6.1 makes collection membership more specific: `BatchDownloadRequest`
and `BatchDownloadItem` retain the collection provider, collection ID, ordered
provider item ID, and position as durable discovery provenance. Before a child
is admitted, `CollectionItemAdmissionResolver` crosses the existing canonical
resolution boundary and produces a provider-neutral canonical admission. The
ordinary child request therefore has no collection-provider affinity; Stage 25
performs global candidate discovery, quality planning, source checking, ranking,
and execution. `MediaArtifactSpec` and SingleFlight remain provider-neutral
technical identities.

## Validation and release procedure

Mandatory deterministic release gates are run from the repository root:

```bash
uv lock --check
UV_PROJECT_ENVIRONMENT=.validation-venv uv sync --locked --extra dev --extra onthespot
uv run ruff format --check .
uv run ruff check .
uv run mypy app
uv run pytest -m "not external" -p no:cacheprovider --basetemp=.pytest-tmp/stage30.5.6-host-<run-id> -ra
bash scripts/validate-production.sh
```

`validate-production.sh` creates or refreshes an ignored, locked
`.validation-venv`, rebuilds current `runtime` and `validation` images,
checks Python/app imports, FFmpeg and ffprobe, runs deterministic media tests
for all five profiles, and runs the credential-free Linux suite. Its zero exit
status and `STAGE30_PROVIDER_PLATFORM_CONTAINER_VALIDATION=PASS` marker are
required evidence; a historical image or marker is not a substitute.

The current 2026-09-11 checkout record is host static **PASS**, host
credential-free **PASS** (`890 passed, 4 host-capability skips`), and focused
platform regressions **PASS** (`222 passed`). Container build/media gates are
**NOT_RUN** because Docker Desktop's Linux engine is unavailable on the
validation host; external smokes are **NOT_RUN** because credentials were not
supplied. This record is intentionally not a Stage 30 completion claim.

External smokes are opt-in and report `PASS`, `FAIL`, or `NOT_RUN`. Missing
credentials, network, or the required local media binary are `NOT_RUN`; an
assertion failure after prerequisites are supplied is `FAIL`. Never print or
commit credential values.

| Provider | Selectable command and prerequisite | Scope |
| --- | --- | --- |
| Spotify | `ONTHESPOT_TEST_SEARCH_PROVIDER=spotify ONTHESPOT_TEST_SEARCH_QUERY='artist title' uv run pytest tests/integration/test_onthespot_external.py::test_real_onthespot_search_when_explicitly_enabled -m external -ra`; configure its existing managed session first | bounded catalog search and metadata |
| Deezer | Same command with `deezer`; configure the existing child-owned ARL first | bounded catalog search and metadata |
| Tidal | Same command with `tidal`; complete its existing device-link session first | bounded catalog search and metadata |
| YouTube Music | `ONTHESPOT_YTM_TEST_TRACK_URL='https://music.youtube.com/watch?v=…' uv run pytest tests/integration/test_onthespot_external.py::test_youtube_music_native_aac128_smoke_when_explicitly_enabled -m external -ra` | public track metadata, source, bounded acquisition, AAC/M4A 128 probe |
| Qobuz | `QOBUZ_EMAIL=… QOBUZ_PASSWORD=… QOBUZ_QUERY='artist title' uv run pytest tests/integration/test_stage303_qobuz_external.py -m external -ra` | existing child-boundary authorization, search, metadata, source check; not an acquisition benchmark |
| Apple Music | `APPLE_MUSIC_MEDIA_USER_TOKEN=… APPLE_MUSIC_EXTERNAL_QUERY='artist title' uv run pytest tests/integration/test_stage3041_apple_music_external.py -m external -ra` | existing child-boundary authorization, search, source, bounded acquisition, AAC/M4A 256 probe, and decrypted provenance |

The Apple and Qobuz values are passed only to the existing sensitive-value and
child IPC authorization paths. They are neither logged nor stored by the
application. The public YouTube Music smoke remains track-only; it does not
introduce cookies, Premium, generic YouTube URLs, albums, or playlists.

For detailed runtime capability evidence, see
[`provider-capability-matrix.md`](provider-capability-matrix.md). For historical
quality, lifecycle, provenance, and recognition regression rationale, see the
Stage 30.5.1 through 30.5.5 documents.
