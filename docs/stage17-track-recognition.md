# Stage 17 — Track Recognition

Stage 17 determines which normalized catalog result best matches a user's stated track intent. It
is an in-memory, deterministic recognition layer only: it neither selects a download source nor
starts download, storage, metadata-enrichment, fingerprinting, embedding, or LLM work.

## Architecture

```text
User query
  + normalized Stage 15/16 search candidates
  -> SearchTracksUseCase.recognize
  -> TrackSearchResult
  -> TrackRecognitionService
  -> RecognitionEngine (RuleBasedRecognitionEngine)
       -> title / artist / duration / album scorers
       -> SimilarityAggregator
       -> RecognitionRanker
       -> ConfidenceResolver
  -> RecognitionResult
```

`app.application.search.SearchTracksUseCase.execute` continues to expose the completed Stage 15
`TrackSearchResult` boundary. Its new `recognize` method performs that search first, converts each
normalized `Track` to a `TrackCandidate`, and delegates the candidate set to
`TrackRecognitionService`. This preserves the search architecture for existing consumers while
giving the current Stage 14 `/search` flow a completed recognition pass. Stage 17 deliberately
does not render a confirmation, selection, or download action in Telegram.

`app.composition.compose_stage9` injects a `TrackRecognitionService` with the current
`RuleBasedRecognitionEngine`. The composition root is the only production assembly change. Stage
16 adapters and their isolated provider-runtime boundary are not changed.

## Domain models

`app.core.recognition` owns immutable recognition-specific values:

- `TrackCandidate`: a Stage 15 `Track`, an opaque source label, and optional immutable search
  metadata. Recognition retains the source for a later presentation layer but never scores,
  branches on, or tie-breaks by provider/source.
- `RecognitionRequest`: a trimmed user query plus candidates. Optional explicit title, artist,
  expected-duration, and album fields support callers that have structured user intent; the Stage
  15 search flow supplies only the query and candidates.
- `SimilarityScores` and `RankedTrackCandidate`: explain each candidate's component scores and
  final score without exposing provider DTOs.
- `RecognitionResult`: the leading candidate, its confidence and `ACCEPT`/`ASK_USER`/`REJECT`
  decision, plus ranked non-leading alternatives. `result.track` is a presentation convenience and
  returns the normalized Stage 15 `Track`.

The existing persisted-track identity matcher is intentionally not reused. It resolves strict,
storage-backed canonical-recording identity using verified metadata; Stage 17 is a separate,
ephemeral catalog-result recognition concern.

## Deterministic scoring and ranking

Each scorer returns a value in the inclusive `0.0`–`1.0` range:

- `TitleSimilarityScorer` compares the explicit requested title when available, otherwise the raw
  query, with the candidate title.
- `ArtistSimilarityScorer` compares explicit requested artists when available, otherwise the raw
  query, against each candidate artist and retains the strongest score.
- `DurationSimilarityScorer` compares optional expected and candidate durations. Missing duration
  on either side is neutral (`0.5`); known durations decay linearly to zero over the isolated
  30-second tolerance.
- `AlbumSimilarityScorer` compares optional expected/candidate albums; missing album metadata is
  neutral (`0.5`).

Text comparison applies Unicode NFKC normalization, case folding, punctuation/whitespace cleanup,
and exact normalized phrase matching. Otherwise it uses the stronger of token F1 and a standard
library sequence ratio. This makes case and normal whitespace/punctuation differences deterministic
without claiming fingerprint-grade recording identity.

`SimilarityAggregator` owns configurable default weights and normalizes their total:

```text
title × 0.45 + artist × 0.35 + duration × 0.15 + album × 0.05
```

`RecognitionRanker` receives only pre-scored candidates and sorts by descending confidence. Equal
scores retain input order through Python's stable sort; it contains no provider ordering or priority
policy.

`ConfidenceResolver` owns the independently configurable decision thresholds:

| Confidence | Decision |
| --- | --- |
| `>= 0.90` | `ACCEPT` |
| `0.60` to `< 0.90` | `ASK_USER` |
| `< 0.60` | `REJECT` |

For example, the query `Daft Punk One More Time` matches a candidate titled `One More Time` by
`Daft Punk` at `0.90` even when Stage 16 has no album/duration data: exact title and artist match,
while the two unavailable optional dimensions are neutral. A `Daft Punk` candidate titled `Around
The World` remains below the rejection threshold.

## Replacement and compatibility seams

`RecognitionEngine` is the only engine abstraction consumed by `TrackRecognitionService`. A later
fingerprint or embedding engine can implement it without changing callers, the result models,
ranking, thresholds, or Stage 16 adapters. Individual scorers, weights, ranker, and resolver are
constructor-injected into `RuleBasedRecognitionEngine`, so future policy changes remain local.

There are no migrations, settings, credentials, database writes, queues, provider API calls, or
download code. The existing Stage 14 private-chat/error boundary and Stage 13 provider-account
security boundary are unchanged. A later presentation boundary may consume `ASK_USER` alternatives;
no interaction or download behavior is defined here.

## Tests

Unit tests cover model validation, independent scorers, neutral missing metadata, aggregation,
ranking, thresholds, engine decisions, alternatives, and service delegation. The integration test
uses an injected in-memory Stage 15 provider to verify the complete normalized search-result to
recognition-result path without an external API.
