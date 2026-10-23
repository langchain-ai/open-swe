# Merge queue runtime

The queue currently forms candidates and records smoke validation. It does **not**
promote them to the target branch. A successful smoke attempt is not a full-suite
pass or permission to merge.

## Configuration

Configuration lives in PostgreSQL, in the nullable `workspace.merge_queue_config`
JSONB column. All workspaces default to NULL, which disables the controller.
An operator can configure one canary workspace with this object; its workspace
ID comes from the row:

```json
{
  "repository": "example/queue-canary",
  "repository_id": 123,
  "dispatch_url": "https://dispatcher.example/api/v1/merge-queue/dispatch",
  "workflow_digest": "<sha256 of the reviewed merge_queue_validate.yml bytes>",
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

These are illustrative identifiers. Use the repository's numeric GitHub ID,
the actual required-check sources, and the gateway's GitHub App bot **user ID**
for `dispatch_actor_id`. The repository must already be bound to that workspace.
Startup refuses multiple configured workspaces. Apply the additive migration
before deploying this runtime; set the column back to SQL NULL to disable it.
Configuration is read at startup, so restart all backend replicas after changes.
Only operators with database access can edit it; the workspace API and agent tools
do not expose this field. Jobs and HTTP callers cannot supply policy, GitHub
credentials, or target repositories.

The existing Open SWE App supplies repository-scoped tokens with Contents write
and Actions, Checks, Metadata, Pull requests and Statuses read. It needs no Actions
write or queue-specific branch-rule bypass. Install the reviewed workflow on the
canary's `main`; the controller verifies its digest before GitHub reconciliation.
Expired database deadlines still reconcile during a GitHub outage.

Workflow dispatch uses a separately deployed gateway. Set the operator-managed
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

## Startup

Registration starts paused. This runtime does not expose queue controls. Enable
only after authenticated controls are installed and ownership handoff is complete.
Pause stops new batch/dispatch work; already dispatched validation and cleanup
can still reconcile.
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
