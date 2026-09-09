# Durable analytics

Open SWE analytics uses an append-only lifecycle event log and derived projections in a dedicated PostgreSQL service boundary. The LangGraph Store remains operational storage and is not queried for reports.

## Deployment contract

Set:

- `ANALYTICS_POSTGRES_URI`: managed PostgreSQL URI. If omitted, custom code reads LangGraph's `POSTGRES_URI` as the deployment-provided database connection. Production operators should provision a separately governed database or schema and prefer the dedicated variable.
- `ANALYTICS_WORKSPACE_ID`: immutable UUID for the workspace.
- `ANALYTICS_EPOCH`: timezone-aware UTC timestamp at which trustworthy collection begins. Events before it are rejected; existing mutable records are not backfilled into a fabricated history.
- `ANALYTICS_ENVIRONMENT`: producer environment, default `production`.
- `ANALYTICS_SUMMARY_VERSION`: one version for metric semantics, attribution, maturity, and histogram buckets. Queries never mix versions.
- `ANALYTICS_PR_MATURITY_DAYS`: default `14`.
- `ANALYTICS_MIN_COHORT_SIZE`: aggregate privacy threshold, default `5`.

The application runs idempotent migrations at startup and exposes `/health/analytics`. The service is product-fail-soft after startup: lifecycle capture catches analytics errors and the product side effect remains successful. PostgreSQL must be provisioned outside this repository with encrypted connections, backups, point-in-time recovery, least-privilege credentials, monitoring, and capacity appropriate to the event volume.

## Delivery and atomicity

Event intents enter the PostgreSQL outbox and are delivered at least once. Workers claim bounded batches with `FOR UPDATE SKIP LOCKED`, retry with bounded exponential backoff and jitter, acknowledge only after the event and projection transaction commits, and retain exhausted rows in `dead_letter`. Admins can inspect stale and dead-letter counts at `/dashboard/api/analytics/outbox-status`.

Most operational state lives in LangGraph Store, GitHub, Slack, or LangGraph runs while analytics lives in PostgreSQL. Those systems cannot participate in one transaction. Therefore an operational side effect may succeed while its outbox insert fails. Capture points are fail-soft and never roll back product work. Deterministic event IDs, provider delivery versions, webhook redelivery, and reconciliation limit loss, but the system does not claim exactly-once side effects across systems.

## Event and privacy contract

The Pydantic envelope rejects unknown fields and validates a specific payload schema for every event name. IDs are deterministic UUIDv5 values. Duplicate ingestion is prevented by database constraints. Corrections are new events with a higher source version or observation revision.

Events contain only opaque workspace-scoped person and subject UUIDs. Prompts, responses, feedback comments, source code, diffs, branch names, raw paths, emails, and display names are prohibited from the event schema. Names live in `identity_directory`, a separately protected join table. Named leaderboard rows are visible only to the person or workspace administrators. Aggregate model cohorts smaller than five are suppressed for non-admins. Repository names live in a protected directory so unauthorized callers can receive redacted dimensions. Named exports must write `named_export_audit` before returning data.

Team-manager authorization is schema-ready through `team_id`, but Open SWE does not currently have an authoritative manager-membership signal. No manager access is granted until such a directory is connected. Effective model is currently captured as configured attribution where provider execution does not return an authoritative effective model.

## Metrics and summaries

Completed UTC days are materialized as versioned summaries; the current UTC day is queried live from the same projections and definitions. Dirty partitions include the event occurrence day plus original PR-open and finding-surfaced cohort days. Recompute atomically replaces one partition.

Summary families include additive counters/sums, PR-open cohorts, finding-surfaced cohorts, cost completeness, exact distinct-member UUID sets, and fixed latency histograms. Distinct counts are computed from set unions, never summed. Range percentiles derive from merged histograms, never averaged daily percentile values.

PR merge rate uses PR-open-date cohorts and snapshots the opening run/model attribution. Outcomes are mutually exclusive: merged, closed without merge, mature pending, and waiting. Decided merge rate excludes pending PRs; mature cohort merge share includes mature pending PRs in the denominator.

Cost observations are per-run and revisioned. `null` is unknown and zero is a valid observed cost. Cost per PR can use the opening run separately and all distinct `pr_run_link_projection` members without duplicate links.

## Signal gaps

Open SWE now captures explicit dashboard task resolution/acceptance, plan rework, PR reopening, run terminals, PR lifecycle, Slack rating sentiment, review publication, and finding lifecycle where those transitions are available. It does not infer acceptance, team identity, major use, effective provider model, or cost when no authoritative signal exists. Feedback text remains only in the operational feedback system and is never copied to analytics.

## Retention and deletion

Defaults are configurable: raw events 25 months, aggregate summaries 7 years, named identities 13 months, acknowledged outbox 30 days, ingestion receipts 90 days, and unresolved dead letters until resolved. Compact event-ID tombstones are retained so an expired raw event cannot be reintroduced by a very late replay. The worker deletes expired online facts and anonymizes identity directory fields in place after their deadline. Cold archives are not created by Open SWE; finance or security may provision an encrypted archive under a separate policy.
