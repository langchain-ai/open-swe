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
  "dispatch_url": "https://dispatcher.example/api/v1/merge-queue/dispatch",
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
existing Open SWE workspace UUID, the actual required-check sources, and the gateway's GitHub App bot **user ID** for `dispatch_actor_id`. The repository must already be bound
to that workspace. Policy changes require a deployment; callers cannot supply
policy, GitHub credentials, or target repositories through jobs or HTTP requests.

The existing Open SWE App supplies repository-scoped tokens with Contents write
and Actions, Checks, Metadata, Pull requests and Statuses read. It needs no Actions
write or queue-specific branch-rule bypass. Install the reviewed workflow on the
canary's `main`; the controller verifies its digest before GitHub reconciliation.
Expired database deadlines still reconcile during a GitHub outage.

Workflow dispatch uses a separately deployed gateway. Set the deployment-owned
HTTPS `dispatch_url` and store `MERGE_QUEUE_DISPATCH_SECRET` (at least 32 random
characters) only in the backend and gateway secret stores. Never pass this secret
to agent sandboxes or general-purpose agent tools. Local agent shells are disabled
wherever it is present. The gateway owns its GitHub credentials and must reject
ordinary agent credentials.

The gateway contract is a POST containing `contract` (the version-1 validation
request) and `expires_at` (the persisted attempt deadline). The
`X-Merge-Queue-Signature` header is the lowercase hex HMAC-SHA256 of the exact UTF-8
body with the shared secret. No redirects or automatic retries are followed.
The gateway must verify the signature, expiry, configured repository and numeric
ID, exact candidate ref/SHA, current main/workflow revision, reviewed workflow
digest and reserved `MERGE_QUEUE_DISPATCH_ACTOR_ID` variable. The workflow actor
is the gateway's bot, not the Open SWE App. Dispatch only
`merge_queue_validate.yml` on `main` with the unchanged `contract` input.

Before sending to GitHub, the gateway commits a durable receipt keyed by attempt
ID and bound to the request body. Duplicate requests never dispatch again, even
following restart or an uncertain GitHub response. It returns 204 for accepted or
already-recorded requests, 4xx for rejected inputs, and 5xx for uncertain outcomes.
An accepted request is not successful validation: Open SWE independently verifies
GitHub runs, jobs and artifacts. Failed delivery can consume an attempt without
starting a run; discovery and the saved deadline handle that case.

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

Enable only after inventorying and stopping the previous queue owner and reconciling
its in-flight work. Import remaining human intent with provenance and fresh
admission; never import passing evidence or leases. Stop old admission, scheduler
and worker paths on every replica. Retain repository access needed by the restricted
gateway, while ensuring no legacy controller can resume. Configuration removal alone
is not an immediate fence for running replicas. Never run two queue owners concurrently.
Keep deployment identifiers, credentials, rulesets and live evidence in private
infrastructure tooling. Promotion requires a separate restricted executor and is
not enabled by this runtime; never grant the agent-accessible App queue bypass.
