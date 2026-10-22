# Merge queue runtime

The queue currently forms candidates and records smoke validation. It does **not**
promote them to the target branch. A successful smoke attempt is not a full-suite
pass or permission to merge.

## Configuration

Unset `MERGE_QUEUE_CONFIG` disables both the API and controller. To enable one
canary repository, supply a trusted JSON object matching `QueueConfiguration`:

```json
{
  "workspace_id": "019a0000-0000-7000-8000-000000000001",
  "repository": "example/queue-canary",
  "repository_id": 123,
  "app_id": 456,
  "installation_id": 789,
  "workflow_digest": "<sha256 of the reviewed merge_queue_validate.yml bytes>",
  "operator_emails": ["operator@example.com"],
  "policy": {
    "checks": {
      "required-check-name": {
        "app_id": 101,
        "workflow_path": ".github/workflows/check.yml"
      }
    },
    "max_batch_size": 1,
    "collection_seconds": 900,
    "validation": {"dispatch_actor_id": 202}
  },
  "retention_days": 90
}
```

These are illustrative identifiers. Use the repository's numeric GitHub ID, an
existing Open SWE workspace UUID, the actual required-check sources, and the queue
App's bot **user ID** for `dispatch_actor_id`. The repository must already be bound
to that workspace. Policy changes require a deployment; callers cannot supply
policy, GitHub credentials, or target repositories through jobs or HTTP requests.

Store `MERGE_QUEUE_APP_PRIVATE_KEY` in the deployment's secret manager. Use a
separate App from ordinary agent sandboxes, with selected-repository installation
access. Its scoped installation tokens request Actions and Contents write;
Checks, Metadata, Pull requests, Statuses and Variables read. The controller checks
the App, installation owner, repository identity, returned token scope and bot ID.
The key is never added to the ordinary App cache or agent credentials. Local
agent shells are disabled wherever this key is present.

Install the reviewed workflow on the canary's `main` and set its
`MERGE_QUEUE_DISPATCH_ACTOR_ID` Actions variable to that bot ID. Review the workflow's
actor guard and all referenced actions/workflows before configuring its digest.
The controller checks the workflow digest and actor variable before each GitHub
reconciliation. Changing either blocks GitHub work until configuration is updated.
Expired database deadlines can still be reconciled during a GitHub outage.

## Human controls

All endpoints use dashboard session cookies and the dashboard mutation-origin
check. Bearer/API-key credentials do not authenticate these endpoints. Access is
limited to the configured workspace and current GitHub repository access.

Under `/dashboard/api/merge-queue/{workspace_id}`:

- `GET` lists active requests, pause state and the active batch ID; `limit` and
  `offset` paginate requests. `GET /batches/{batch_id}` shows candidate and attempt status.
- `POST /requests` accepts `number`, `head_sha`, an idempotency UUID `request_key`,
  and optional PR-number `dependencies`. It requires repository write access and
  records intent before reviews finish. The worker separately checks admission.
- `DELETE /requests/{request_id}` withdraws the caller's intent. Operators may
  withdraw another person's intent. Withdrawal invalidates an affected batch.
- `PATCH /pause` accepts `paused`; `PATCH /requests/{request_id}/priority` accepts
  `priority`. Both require an operator email from the current stored user identity
  and repository write access. Priority changes only affect unbatched requests.

Registration starts paused. An operator resumes explicitly. Pause stops new
batch/dispatch work; already dispatched validation and cleanup can still reconcile.
Startup registers a keyed minute job, retaining existing requests, pause state,
and running job claims. GitHub failures are retried by subsequent reconciliation;
there is no one-time GitHub availability requirement during startup.

## Ownership handoff

Enable only after inventorying and stopping the previous owner, reconciling its
in-flight work, and revoking its access to this repository. Import any remaining
human intent with provenance and fresh admission; do not import passing evidence
or leases. Configuration removal is not an immediate fence for already running
replicas: stop all old replicas and revoke their repository access before handing
over. Never run old and new owners concurrently. Keep repository-specific rollout
steps, credentials, rulesets and live evidence in private infrastructure tooling.
