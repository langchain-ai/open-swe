# Durable invocation cost recovery

New terminal analytics events and their cost jobs commit in one PostgreSQL transaction. Failure logs use `terminal_commit_failed`; alert on this because recovery cannot reconstruct a terminal transaction that never committed. Historical backfill is not included. Existing marker rows become `legacy_unmapped`; queued scheduler payloads supply their original lookup context and upgrade only those rows. Duplicate callbacks do not reset jobs or start scheduler chains.

## Rollout

Deploy the migration and compatibility handler to every replica with `COST_RECOVERY_ENABLED=false` (default). New jobs accumulate even while processing is disabled. Enable with `COST_RECOVERY_ENABLED=true` after checking queue state. A separate async worker performs lookups; analytics delivery remains independent.

The database-wide dispatcher permits one lookup at a time with a three-minute lease and a two-minute lookup timeout. `COST_RECOVERY_SPACING_SECONDS` defaults to 15, with a minimum of one second, measured after a lookup finishes. Expired leases allow crash recovery; claim tokens fence stale writes. A process suspended beyond its lease can finish an old remote request, but cannot persist its result. Rate limits pause the dispatcher for at least the retry delay and any Retry-After. Slack cost lookups are unchanged and still consume shared capacity.

Initial lookup is due after 15 seconds. Subsequent delays are 30/60/120/240 seconds, 15 minutes, one hour, six hours, then daily, with 20% jitter. Seven days from enqueue (or explicit requeue) ends automatic retries in `needs_attention`. Missing data is never zero; an observed zero is valid. Configuration/permission and invalid identifier failures require repair. Project resolution and transient failures retry without negatively caching project discovery.

A successful observation is enqueued with a deterministic event ID in the same transaction that sets `awaiting_delivery`. No further lookup occurs while delivery is pending. An existing non-null cost projection satisfies recovery; dead-letter delivery becomes `needs_attention`. Requeue repairs that same outbox event instead of issuing a new lookup.

## Operator commands

Run with the deployment's database credentials, analytics identity configuration and Python dependencies. These commands are intentionally not public API endpoints or agent tools. Access is restricted to database operators; the supplied workspace must match the deployment's analytics workspace UUID, not a sandbox workspace slug.

```sh
python -m agent.analytics.cost_recovery_ops status --workspace <analytics-workspace-uuid>
python -m agent.analytics.cost_recovery_ops requeue --workspace <analytics-workspace-uuid> --run <opaque-analytics-run-uuid>
```

Status reports counts by state and sanitized reason, aggregate attempts, retried jobs and oldest due-job seconds. Sample these over time for retry/exhaustion rates; monitor `needs_attention`, `legacy_unmapped`, aged due jobs and `terminal_commit_failed`. Requeue only after correcting the underlying problem. It resets the seven-day retry budget for the selected needs-attention row. There is no bulk requeue or historical lookup-ID reconstruction.

## Retention and rollback

Raw invocation/thread IDs, invocation start time and project name live only in `run_cost_refresh`, not analytics events, exports, status output or application logs. PUBLIC privileges on operational tables are revoked. Database owners must exclude these tables from reporting/export roles and grant reporting roles only explicit projection access; database owners and existing broad grants are not constrained by PUBLIC revocation.

The worker removes raw context one day after successful completion or 30 days after original enqueue for unresolved needs-attention jobs. Maintenance runs even with lookup processing disabled. Minimal opaque IDs, event IDs and diagnostics remain. A requeue does not extend the original retention deadline. Jobs whose lookup context has been purged cannot be requeued for lookup; delivery-only repair remains possible.

To pause, disable the flag on all replicas and allow an in-flight lookup up to two minutes to finish. Do not roll back to scheduler-only code while durable pending jobs exist: first drain the queue or leave a compatible recovery worker running. Do not drop the additive migration or operational rows. Disabled deployments continue to age jobs toward the seven-day needs-attention horizon. Rollout verification should include one deliberately deferred new job recovering, no duplicate cost counting, and no material increase in 429s; track historical unmapped rows separately.
