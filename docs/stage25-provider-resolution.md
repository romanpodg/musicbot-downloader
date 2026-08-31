# Stage 25 — Smart provider resolution

Stage 25 adds a provider-independent `CanonicalMediaIdentity`, conservative
Unicode/case/whitespace normalization, exact ISRC matching, and strong metadata
matching with a three-second duration tolerance. Semantic version markers
(live, remix, remaster, acoustic, radio edit, instrumental, karaoke, demo,
sped-up/slowed, and similar forms) are retained; a version mismatch vetoes
automatic fallback unless an exact ISRC proves the recording identity.

`ProviderCandidateResolver` queries only the existing `MusicProvider` boundary,
normalizes bounded metadata results, and persists a compact candidate snapshot in
`download_provider_candidates` with a uniqueness constraint. Candidates are
ranked deterministically by match confidence, source-provider preference,
configured priority, health, and stable provider/media identifiers. Capability
filters reuse Stage 22 media/profile semantics, including exact replay fidelity.

Account health is separate from candidate identity. The application-owned
`account_health` projection stores only provider/account IDs and health
metadata (never credentials): state, failure streak, success/failure timestamps,
and cooldown deadline. `ProviderAccountSelector.eligible_durable` reads this
projection on every selection, preserving caller-provided fairness order across
restart. Rate limits persist a deterministic cooldown; authentication failures
remain in `AUTH_FAILED` until normal provider reauthentication makes the account
usable; successful execution resets the streak and returns the account to
`HEALTHY`. Account-local failures can move to another account, while media or
provider-temporary failures may move to the next candidate. Processing and
Telegram delivery failures never trigger provider fallback. Every attempt is
auditable in `download_provider_attempts`, while all attempts remain inside one
Stage 21 `DownloadJob` lifecycle; expired lifecycle leases reconcile unfinished
attempts as `ABANDONED`.

Stage 24 cache lookup remains first: cache HIT bypasses candidate resolution;
only a legitimate cache MISS reaches Stage 25. Album children use the same
single lifecycle automatically, and history/cache remain provider-specific.

Playlists, user provider preference UI, cross-provider cache canonicalization,
persistent local media, new providers solely for fallback, and Stage 26+
functionality remain intentionally out of scope.
