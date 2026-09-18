# Durable PR distance measurements

## Contract and capture

`pr.distance_measured` is an independent analytics event, not a merge or a lifecycle
correction. `emitter.pr_distance_measured(payload, measured_at=...)` is the internal
async producer interface. It uses the existing outbox, does not suppress failures,
and does not assign a lifecycle `source_version`. There is no public endpoint or
backfill job.

A successful measurement requires a strict integer in **0..10000**, canonical
lowercase `owner/repo`, a positive PR number, four full Git commit SHAs (opening
base/head and final base/head), algorithm revision, and two opaque UUID evidence
references. Zero is measured; missing is not an event. Ingestion checks the opaque
repository and PR identities against the payload and rejects lifecycle versions.
Extra fields, arbitrary URLs, and raw traces/diffs are not accepted. Do not put
credentials, code, or trace contents in evidence records or analytics payloads.

The webhook emits the real lifecycle transition first. A successful optional
comparison then emits a measurement with the original `pull_request.id` as the
opening evidence reference and the deterministic `pr.merged` event ID as the final
evidence reference. The persisted measurement contains the exact revisions used;
these UUID references are audit pointers, not extra copies of the source records.
The source records themselves can expire. Capture/measurement errors are logged
without suppressing the merge event. No comparison algorithm changes are made:
`myers-line-v1` labels the current `agent/analytics/distance.py` implementation
(including completeness checks and budgets, as of main `ebd88cdd`).

This is a trusted internal evidence boundary, not an independent proof verifier.
The normal producer uses opening-time captured revisions and the merged webhook's
final revisions. Payload validation alone does not establish historical provenance.
Unknown opening revisions remain unknown; neither current branch tips nor inferred
historical revisions are substitutes.

## Persistence, ordering, and conflicts

Migration `0021` creates `pr_distance_measurements`, a durable table keyed by
`(workspace_id, pr_id)` with no dependency on a projection row or expiring event
row. It retains the complete typed measurement, repository identity, first event
ID, measurement time, and record time. It deliberately does not populate existing
rows or infer historical provenance.

Ingestion serializes on the same PR advisory lock used by lifecycle processing.
Evidence, event identity, raw event, counts, receipts, distance cache, and dirty
partitions commit in one transaction. Exact payload duplicates are no-ops even
with a different transport event ID or measurement timestamp; they may repair the
distance cache but do not increment counts. Evidence equality includes all four
SHAs, value, algorithm, repository/PR, and both evidence references.

**First committed verified evidence wins; conflicting evidence is rejected.**
A different value, revision, or evidence reference raises `PRDistanceConflictError`
and rolls back ingestion without changing the retained measurement or lifecycle.
Conflicts do not silently overwrite or pick the latest timestamp. Outbox delivery
uses its existing retry/dead-letter path; operators must review conflicts rather
than delete the accepted evidence or retry with a made-up lifecycle version.
Concurrent conflicting submissions have the same first-commit policy; arrival
order is intentionally not a mechanism for deciding which evidence is true.
There is no automatic correction/supersession API in this change.

Distance projection is separate from lifecycle ordering. It only writes
`pr_projection.distance_basis_points`, and only exposes a value while the PR is
merged. Open, reopened, and closed-without-merge PRs retain evidence but expose
NULL distance. Measurement delivery does not create an opening, change model
attribution, outcomes, transition timestamps, source versions, links, or tasks.
Opening reconciliation and lifecycle replay restore the cache from durable
measurement evidence, including when evidence arrived before opening. Existing
usage queries continue counting non-NULL distances only for merged PRs; a zero
contributes to both sample size and median.

Legacy `PRStatePayload.distance_basis_points` remains readable. A NULL merged
replay no longer clears an existing value; absent verified evidence, the first
non-NULL cached legacy value is preserved across merged replays. A real nonmerged
transition still clears that cache. Legacy measurements are not promoted to
verified evidence, because their four revisions and evidence references were not
recorded. New verified evidence takes precedence over this legacy-only cache.

## Retention and supported recovery

Raw events expire based on `occurred_at`, not ingestion time. Therefore even a
newly ingested event with an old occurrence time may disappear immediately at
retention. Acknowledged outbox rows and receipts also expire. None of these deletes
remove `pr_distance_measurements`; event deduplication IDs also remain durable.
The measurement table must be included in database backups and must **not** be
truncated as an expendable projection.

Supported recovery is **distance-cache recovery**, not a full analytics rebuild:

- Normal PR opening/lifecycle processing restores distance from retained evidence.
- An authorized internal repair can call
  `restore_pr_distance(conn, workspace_id=..., pr_id=...)` in its own transaction
  for an existing projection. It restores merged values (or NULL for nonmerged
  states) without changing lifecycle fields or counts. Serialize repairs with
  ingestion using the same `analytics:{workspace_id}:{pr_id}` advisory lock, or
  quiesce ingestion for an offline repair.
- Exact measurement redelivery also repairs that cache, without requiring raw
  events or adding event counts.
- If a PR projection must be recreated while its opening and lifecycle evidence
  are still available, replay through the projection reconciliation path, then
  restore its distance from the durable table. Calling `ingest` on already-seen
  lifecycle event IDs is deduplicated and is not a projection rebuild API.

Deleting the durable table cannot be repaired from expired raw events. Nor can
this feature reconstruct a deleted opening/lifecycle projection after those raw
records expire: preserve/restore the durable lifecycle projection from backups.
A lifecycle outcome delivered before opening can still be lost if its raw event
expires before opening; this existing lifecycle limitation is not fixed by
inventing merge events from measurement evidence. Legacy cache-only distances
have no durable verified recovery once their cache and source events are gone.

## Future reviewed backfill

A future restricted, reviewed backfill API may submit this same typed payload
through the internal producer after verifying repository/PR identity, original
opening and final revisions, evidence ownership, completeness, and the exact
algorithm revision. It should include dry-run validation, reviewer/audit evidence
references, and an explicit separately reviewed correction policy for conflicts.
It must not emit synthetic openings/merges, advance lifecycle versions, infer
opening SHAs from current tips, or use an unexplained integer as evidence.
This change neither implements nor executes historical backfill, a public write
endpoint, or production migration/repair operations.
