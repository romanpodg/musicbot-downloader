# Stage 30.5.1 — Provider Resolution & Quality Orchestration

Stage 25 previously ranked recognition/source-provider candidates before the
quality resolver ran. A Qobuz recognition could therefore select a Qobuz FLAC
candidate for application transcoding before a YouTube Music AAC128 source with
an exact direct plan was considered.

The production ordering is now:

1. resolve candidate sources;
2. resolve currently feasible quality plans for every available source;
3. order candidates by the existing `QualityResolver` plan class;
4. apply match score, recognition/source-provider affinity, optional provider
   priority, and stable provider/media identifiers as lower-priority ties;
5. select accounts and acquire media.

`QualityResolver` and `plan_sort_key` remain the single semantic authority. The
ordering is DIRECT confirmed, DIRECT preflight, confirmed lossless-to-requested
lossy transcode, then transcode preflight. Candidates without a feasible plan
are not executable. `ProviderResolver.check_source()` remains authoritative for
source availability; provider health alone does not make a source executable.

Account behavior is unchanged: all eligible accounts for the selected provider
are attempted before moving to the next globally ranked provider. Provider
failures eligible for fallback advance to the next plan, and each real attempt
is persisted once in `ProviderAttempt` order. Processing, artifact, and Telegram
delivery failures remain outside provider fallback.

This stage does not change provider manifests or activation, authorization,
quality safety, recognition scoring, cache or SingleFlight semantics, account
lifecycle, migrations, dependencies, or the pinned OnTheSpot revision. Bandcamp
and SoundCloud remain deferred.
