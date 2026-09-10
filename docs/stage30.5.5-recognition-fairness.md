# Stage 30.5.5 — Recognition Fairness Hardening

Stage 30.5.5 corrects a bounded-enrichment fairness defect in Recognition 2.0.
Previously, preliminary candidates were ranked and the first five raw provider
rows were enriched. Five provider copies of recording A could therefore consume
all five calls before a distinct recording B was considered. If B appeared in
the sixth row, its available duration, album, or other provider-neutral evidence
was excluded and the recognition decision could change with provider order.

Fairness means that permuting equivalent provider evidence does not materially
change the recognized recording, the ACCEPT/ASK_USER/REJECT decision, distinct
recording ambiguity, or variant separation. The ephemeral representative row
for a same-recording cluster may differ; that is a representative-only
difference and is not semantic identity.

## Bounded, recording-aware selection

Before metadata calls, the service performs a conservative provisional grouping
using the existing provider-neutral `match_track_identities` semantics. A
candidate joins a provisional group only when the existing matcher says
`MATCHED`; unknown or conflicting evidence remains separate. This preserves
hard ISRC, version, explicitness, duration, title, and artist conflicts without
introducing a second fuzzy identity algorithm.

Selection is deterministic and has two passes:

1. **Diversify:** select the highest-ranked candidate from each provisional
   distinct-recording group, in preliminary rank order.
2. **Fill:** if capacity remains, walk the original ranking and select the next
   unselected candidates until the existing budget of five is full.

Thus five calls remain available when five usable candidates exist, while a
same-recording provider fan-out cannot monopolize the first pass. There are
never more than five metadata-enricher calls per recognition request, and no
provider quotas, randomization, or provider-order scoring are used.

Provisional groups are in-memory budget-allocation aids only. They do not create
canonical recording identity, persist `Track` or `TrackSource`, or decide a
recognition result. After enrichment, the existing Stage 29 final clustering,
provider-neutral score weights, ambiguity margin, and decision policy remain
authoritative. Rich metadata can therefore improve confidence normally, and
final enriched evidence can still split or merge provisional groups.

## Audited six-row example

The regression fixture contains five provider copies of A and one distinct B.
When B was raw row six, B was not enriched and the result could be A/ASK_USER;
when B was in the raw top five, its richer metadata raised it to B/ACCEPT. With
recording-aware diversification, B is selected in the first pass regardless of
where it appears, before spare capacity is spent on additional A copies. The
normal scoring and decision logic then yields the same semantic result for all
tested provider permutations.

This stage does not change search order, download-provider resolution, quality
semantics, lifecycle, cache/SingleFlight behavior, persistence boundaries,
providers, migrations, dependencies, or Stage 30.5.6 production validation.
